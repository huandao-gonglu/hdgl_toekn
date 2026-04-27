package provider

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/Wei-Shaw/sub2api/internal/payment"
)

func TestFastPaySignMatchesServerRule(t *testing.T) {
	t.Parallel()

	params := map[string]string{
		"merchantNo": "M001",
		"outTradeNo": "ORDER123",
		"shopNo":     "S001",
		"payType":    "alipay",
		"amount":     "10.00",
		"subject":    "Sub2API Topup",
		"timestamp":  "1700000000",
		"returnUrl":  "https://return.example/payment/result",
	}

	got := fastPaySign(params, "secret")
	if got != "3FED816B091A7147E13A5C87C1B1E820" {
		t.Fatalf("fastPaySign = %q", got)
	}
}

func TestFastPayCreatePaymentPostsSignedJSONAndReturnsHostedPage(t *testing.T) {
	t.Parallel()

	var gotPath string
	var gotReq map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		if r.Method != http.MethodPost {
			t.Fatalf("method = %s, want POST", r.Method)
		}
		if err := json.NewDecoder(r.Body).Decode(&gotReq); err != nil {
			t.Fatalf("decode request: %v", err)
		}
		sign, _ := gotReq["sign"].(string)
		if sign == "" {
			t.Fatal("missing sign")
		}
		params := map[string]string{}
		for k, v := range gotReq {
			if s, ok := v.(string); ok {
				params[k] = s
			}
		}
		if !fastPayVerifySign(params, "api-secret", sign) {
			t.Fatalf("invalid sign for request: %#v", gotReq)
		}

		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"code":200,"message":"ok","data":{"orderNo":"FP202604270001","outTradeNo":"sub2-001","amount":10.00,"payType":"alipay","payMethod":"api","qrcodeUrl":"https://cdn.example/qr.png","expireTime":1777278000}}`))
	}))
	defer server.Close()

	fp := newTestFastPay(t, server.URL+"/fastpay-server")
	resp, err := fp.CreatePayment(context.Background(), payment.CreatePaymentRequest{
		OrderID:     "sub2-001",
		Amount:      "10.00",
		PaymentType: payment.TypeAlipay,
		Subject:     "Sub2API Topup",
		NotifyURL:   "https://sub2.example/api/v1/payment/webhook/fastpay",
		ReturnURL:   "https://sub2.example/payment/result",
	})
	if err != nil {
		t.Fatalf("CreatePayment returned error: %v", err)
	}
	if gotPath != "/fastpay-server/api/pay/create" {
		t.Fatalf("path = %q, want /fastpay-server/api/pay/create", gotPath)
	}
	for key, want := range map[string]string{
		"merchantNo": "M001",
		"shopNo":     "S001",
		"outTradeNo": "sub2-001",
		"amount":     "10.00",
		"payType":    "alipay",
		"subject":    "Sub2API Topup",
		"notifyUrl":  "https://sub2.example/api/v1/payment/webhook/fastpay",
		"returnUrl":  "https://sub2.example/payment/result",
	} {
		if got, _ := gotReq[key].(string); got != want {
			t.Fatalf("request[%s] = %q, want %q", key, got, want)
		}
	}
	if resp.TradeNo != "FP202604270001" {
		t.Fatalf("TradeNo = %q", resp.TradeNo)
	}
	if resp.PayURL != server.URL+"/fastpay-merchant/pay/FP202604270001" {
		t.Fatalf("PayURL = %q", resp.PayURL)
	}
	if resp.QRCode != "" {
		t.Fatalf("QRCode should be empty for hosted-page flow, got %q", resp.QRCode)
	}
}

func TestFastPayVerifyNotificationParsesSignedForm(t *testing.T) {
	t.Parallel()

	fp := &FastPay{config: map[string]string{"apiSecret": "api-secret", "merchantNo": "M001"}}
	params := map[string]string{
		"merchantNo": "M001",
		"orderNo":    "FP202604270001",
		"outTradeNo": "sub2-001",
		"amount":     "10.00",
		"payAmount":  "10.00",
		"payType":    "alipay",
		"status":     "1",
		"payTime":    "2026-04-27T15:10:00",
		"timestamp":  "1777273800",
	}
	params["sign"] = fastPaySign(params, "api-secret")

	form := url.Values{}
	for k, v := range params {
		form.Set(k, v)
	}

	notification, err := fp.VerifyNotification(context.Background(), form.Encode(), nil)
	if err != nil {
		t.Fatalf("VerifyNotification returned error: %v", err)
	}
	if notification.TradeNo != "FP202604270001" || notification.OrderID != "sub2-001" {
		t.Fatalf("notification IDs = %#v", notification)
	}
	if notification.Status != payment.ProviderStatusSuccess {
		t.Fatalf("Status = %q", notification.Status)
	}
	if notification.Amount != 10 {
		t.Fatalf("Amount = %v", notification.Amount)
	}
	if notification.Metadata["merchantNo"] != "M001" {
		t.Fatalf("metadata = %#v", notification.Metadata)
	}

	tampered := strings.Replace(form.Encode(), "10.00", "99.99", 1)
	if _, err := fp.VerifyNotification(context.Background(), tampered, nil); err == nil {
		t.Fatal("VerifyNotification should reject tampered form")
	}
}

func newTestFastPay(t *testing.T, apiBase string) *FastPay {
	t.Helper()
	fp, err := NewFastPay("test-fastpay", map[string]string{
		"merchantNo": "M001",
		"apiSecret":  "api-secret",
		"shopNo":     "S001",
		"apiBase":    apiBase,
		"notifyUrl":  "https://sub2.example/api/v1/payment/webhook/fastpay",
		"returnUrl":  "https://sub2.example/payment/result",
	})
	if err != nil {
		t.Fatalf("NewFastPay: %v", err)
	}
	return fp
}
