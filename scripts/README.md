# 本地部署脚本

适用于 Windows/macOS/Linux。本机需要已安装 Python、Go、pnpm、ssh/scp、curl。

脚本入口是 `deploy_sub2api_local.py`，不再依赖 shell 包装脚本。macOS/Linux 可直接执行：

```bash
./scripts/deploy_sub2api_local.py --dry-run
```

## 预览命令

```powershell
python scripts/deploy_sub2api_local.py --dry-run
```

## 编译并部署

```powershell
python scripts/deploy_sub2api_local.py
```

等价于：

```powershell
python scripts/deploy_sub2api_local.py --mode deploy
```

默认会执行：

1. 使用 pnpm 编译前端。
2. 在本机交叉编译 Linux 后端。
3. 打包运行文件。
4. 上传到远程服务器。
5. 在远程服务器构建 Docker 运行镜像。
6. 重启 `sub2api` 服务。
7. 执行健康检查。

## 只打包

```powershell
python scripts/deploy_sub2api_local.py --mode package --archive dist/sub2api-runtime.tgz
```

只执行本地前端构建、Go 交叉编译和运行包归档，不上传、不重启远端服务。

## 只发布

```powershell
python scripts/deploy_sub2api_local.py --mode publish --archive dist/sub2api-runtime.tgz
```

只上传已有运行包，在远端构建运行镜像并重启服务，不重新执行本地前端或 Go 编译。

## 测试 dry-run 输出

```powershell
python scripts/test_deploy_sub2api_local.py
```
