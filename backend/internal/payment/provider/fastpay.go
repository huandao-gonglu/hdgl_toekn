package provider

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/md5"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/payment"
)

const (
	fastPayHTTPTimeout     = 10 * time.Second
	fastPaySuccessCode     = 200
	fastPayPaidStatus      = "1"
	maxFastPayResponseSize = 1 << 20
)

// FastPay implements payment.Provider for X-TQ/bigbear-fastpay.
type FastPay struct {
	instanceID string
	config     map[string]string
	httpClient *http.Client
}

// NewFastPay creates a new FastPay provider.
// Required config keys: merchantNo, apiSecret, shopNo, apiBase, notifyUrl, returnUrl.
// Optional: payPageBase.
func NewFastPay(instanceID string, config map[string]string) (*FastPay, error) {
	for _, k := range []string{"merchantNo", "apiSecret", "shopNo", "apiBase", "notifyUrl", "returnUrl"} {
		if strings.TrimSpace(config[k]) == "" {
			return nil, fmt.Errorf("fastpay config missing required key: %s", k)
		}
	}
	cfg := make(map[string]string, len(config))
	for k, v := range config {
		cfg[k] = strings.TrimSpace(v)
	}
	cfg["apiBase"] = normalizeFastPayAPIBase(cfg["apiBase"])
	if cfg["payPageBase"] == "" {
		cfg["payPageBase"] = deriveFastPayPageBase(cfg["apiBase"])
	}
	return &FastPay{
		instanceID: instanceID,
		config:     cfg,
		httpClient: &http.Client{Timeout: fastPayHTTPTimeout},
	}, nil
}

func normalizeFastPayAPIBase(apiBase string) string {
	base := strings.TrimSpace(apiBase)
	if base == "" {
		return ""
	}
	if parsed, err := url.Parse(base); err == nil && parsed.Scheme != "" && parsed.Host != "" {
		parsed.RawQuery = ""
		parsed.Fragment = ""
		parsed.RawPath = ""
		parsed.Path = trimFastPayEndpointPath(parsed.Path)
		return strings.TrimRight(parsed.String(), "/")
	}
	return strings.TrimRight(trimFastPayEndpointPath(base), "/")
}

func trimFastPayEndpointPath(path string) string {
	path = strings.TrimRight(strings.TrimSpace(path), "/")
	lower := strings.ToLower(path)
	for _, suffix := range []string{"/api/pay/create", "/api/pay/query", "/api/pay/submit"} {
		if strings.HasSuffix(lower, suffix) {
			return strings.TrimRight(path[:len(path)-len(suffix)], "/")
		}
	}
	return path
}

func deriveFastPayPageBase(apiBase string) string {
	parsed, err := url.Parse(apiBase)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return ""
	}
	parsed.RawQuery = ""
	parsed.Fragment = ""
	parsed.RawPath = ""
	parsed.Path = strings.TrimRight(parsed.Path, "/")
	if strings.HasSuffix(parsed.Path, "/fastpay-server") {
		parsed.Path = strings.TrimSuffix(parsed.Path, "/fastpay-server") + "/fastpay-merchant"
		return strings.TrimRight(parsed.String(), "/")
	}
	return ""
}

func (f *FastPay) Name() string        { return "FastPay" }
func (f *FastPay) ProviderKey() string { return payment.TypeFastPay }
func (f *FastPay) SupportedTypes() []payment.PaymentType {
	return []payment.PaymentType{payment.TypeAlipay, payment.TypeWxpay}
}

func (f *FastPay) MerchantIdentityMetadata() map[string]string {
	if f == nil {
		return nil
	}
	merchantNo := strings.TrimSpace(f.config["merchantNo"])
	if merchantNo == "" {
		return nil
	}
	return map[string]string{"merchantNo": merchantNo}
}

func (f *FastPay) CreatePayment(ctx context.Context, req payment.CreatePaymentRequest) (*payment.CreatePaymentResponse, error) {
	notifyURL, returnURL := f.resolveURLs(req)
	params := map[string]string{
		"merchantNo": f.config["merchantNo"],
		"outTradeNo": req.OrderID,
		"shopNo":     f.config["shopNo"],
		"amount":     req.Amount,
		"subject":    req.Subject,
		"payType":    payment.GetBasePaymentType(req.PaymentType),
		"timestamp":  strconv.FormatInt(time.Now().Unix(), 10),
		"notifyUrl":  notifyURL,
		"returnUrl":  returnURL,
	}
	params["sign"] = fastPaySign(fastPayCreateSignParams(params), f.config["apiSecret"])

	body, err := f.postJSON(ctx, f.config["apiBase"]+"/api/pay/create", params)
	if err != nil {
		return nil, fmt.Errorf("fastpay create: %w", err)
	}
	var resp fastPayResult[fastPayCreateData]
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("fastpay parse create: %w", err)
	}
	if resp.Code != fastPaySuccessCode {
		return nil, fmt.Errorf("fastpay create error: %s", resp.Message)
	}
	payURL := ""
	if pageBase := strings.TrimRight(f.config["payPageBase"], "/"); pageBase != "" && resp.Data.OrderNo != "" {
		payURL = pageBase + "/pay/" + url.PathEscape(resp.Data.OrderNo)
	}
	return &payment.CreatePaymentResponse{
		TradeNo: resp.Data.OrderNo,
		PayURL:  payURL,
	}, nil
}

func (f *FastPay) resolveURLs(req payment.CreatePaymentRequest) (string, string) {
	notifyURL := strings.TrimSpace(req.NotifyURL)
	if notifyURL == "" {
		notifyURL = f.config["notifyUrl"]
	}
	returnURL := strings.TrimSpace(req.ReturnURL)
	if returnURL == "" {
		returnURL = f.config["returnUrl"]
	}
	return notifyURL, returnURL
}

func (f *FastPay) QueryOrder(ctx context.Context, tradeNo string) (*payment.QueryOrderResponse, error) {
	q := url.Values{}
	q.Set("merchantNo", f.config["merchantNo"])
	q.Set("outTradeNo", strings.TrimSpace(tradeNo))
	body, err := f.get(ctx, f.config["apiBase"]+"/api/pay/query?"+q.Encode())
	if err != nil {
		return nil, fmt.Errorf("fastpay query: %w", err)
	}
	var resp fastPayResult[fastPayQueryData]
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("fastpay parse query: %w", err)
	}
	if resp.Code != fastPaySuccessCode {
		return nil, fmt.Errorf("fastpay query error: %s", resp.Message)
	}
	statusValue := fastPayAnyString(resp.Data.Status)
	status := payment.ProviderStatusPending
	if statusValue == fastPayPaidStatus {
		status = payment.ProviderStatusPaid
	}
	amount, _ := strconv.ParseFloat(firstNonEmpty(fastPayAnyString(resp.Data.PayAmount), fastPayAnyString(resp.Data.Amount)), 64)
	return &payment.QueryOrderResponse{
		TradeNo:  firstNonEmpty(resp.Data.OrderNo, tradeNo),
		Status:   status,
		Amount:   amount,
		PaidAt:   resp.Data.PayTime,
		Metadata: f.MerchantIdentityMetadata(),
	}, nil
}

func (f *FastPay) VerifyNotification(_ context.Context, rawBody string, _ map[string]string) (*payment.PaymentNotification, error) {
	values, err := url.ParseQuery(rawBody)
	if err != nil {
		return nil, fmt.Errorf("parse fastpay notify: %w", err)
	}
	params := make(map[string]string, len(values))
	for k := range values {
		params[k] = values.Get(k)
	}
	sign := params["sign"]
	if sign == "" {
		return nil, fmt.Errorf("missing sign")
	}
	if !fastPayVerifySign(params, f.config["apiSecret"], sign) {
		return nil, fmt.Errorf("invalid signature")
	}
	if merchantNo := strings.TrimSpace(f.config["merchantNo"]); merchantNo != "" && params["merchantNo"] != merchantNo {
		return nil, fmt.Errorf("merchantNo mismatch")
	}

	status := payment.ProviderStatusFailed
	if strings.TrimSpace(params["status"]) == fastPayPaidStatus {
		status = payment.ProviderStatusSuccess
	}
	amount, _ := strconv.ParseFloat(firstNonEmpty(params["payAmount"], params["amount"]), 64)
	metadata := f.MerchantIdentityMetadata()
	if metadata == nil {
		metadata = map[string]string{}
	}
	if payType := strings.TrimSpace(params["payType"]); payType != "" {
		metadata["payType"] = payType
	}
	return &payment.PaymentNotification{
		TradeNo:  params["orderNo"],
		OrderID:  params["outTradeNo"],
		Amount:   amount,
		Status:   status,
		RawData:  rawBody,
		Metadata: metadata,
	}, nil
}

func (f *FastPay) Refund(context.Context, payment.RefundRequest) (*payment.RefundResponse, error) {
	return nil, fmt.Errorf("fastpay refund is not supported")
}

func (f *FastPay) postJSON(ctx context.Context, endpoint string, payload map[string]string) ([]byte, error) {
	data, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	return f.do(req)
}

func (f *FastPay) get(ctx context.Context, endpoint string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	return f.do(req)
}

func (f *FastPay) do(req *http.Request) ([]byte, error) {
	client := f.httpClient
	if client == nil {
		client = &http.Client{Timeout: fastPayHTTPTimeout}
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxFastPayResponseSize))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, summarizeEasyPayResponse(body))
	}
	return body, nil
}

type fastPayResult[T any] struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    T      `json:"data"`
}

type fastPayCreateData struct {
	OrderNo    string `json:"orderNo"`
	OutTradeNo string `json:"outTradeNo"`
	QrcodeURL  string `json:"qrcodeUrl"`
	ExpireTime int64  `json:"expireTime"`
}

type fastPayQueryData struct {
	OrderNo    string `json:"orderNo"`
	OutTradeNo string `json:"outTradeNo"`
	Amount     any    `json:"amount"`
	PayAmount  any    `json:"payAmount"`
	Status     any    `json:"status"`
	PayTime    string `json:"payTime"`
}

func fastPaySign(params map[string]string, apiSecret string) string {
	keys := make([]string, 0, len(params))
	for k := range params {
		if k == "sign" {
			continue
		}
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var buf strings.Builder
	for i, k := range keys {
		if i > 0 {
			_ = buf.WriteByte('&')
		}
		_, _ = buf.WriteString(k + "=" + params[k])
	}
	_, _ = buf.WriteString("&key=" + apiSecret)
	hash := md5.Sum([]byte(buf.String()))
	return strings.ToUpper(hex.EncodeToString(hash[:]))
}

func fastPayVerifySign(params map[string]string, apiSecret string, sign string) bool {
	return hmac.Equal([]byte(fastPaySign(params, apiSecret)), []byte(strings.ToUpper(strings.TrimSpace(sign))))
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

func fastPayAnyString(v any) string {
	switch val := v.(type) {
	case string:
		return strings.TrimSpace(val)
	case float64:
		return strconv.FormatFloat(val, 'f', -1, 64)
	case int:
		return strconv.Itoa(val)
	case int64:
		return strconv.FormatInt(val, 10)
	case json.Number:
		return val.String()
	default:
		return ""
	}
}

func fastPayCreateSignParams(params map[string]string) map[string]string {
	signParams := make(map[string]string, 8)
	for _, key := range []string{"merchantNo", "outTradeNo", "shopNo", "payType", "amount", "subject", "timestamp", "returnUrl"} {
		if value := strings.TrimSpace(params[key]); value != "" {
			signParams[key] = value
		}
	}
	return signParams
}
