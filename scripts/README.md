# 本地部署脚本

适用于 Windows/macOS/Linux。本机需要已安装 Python、Go、pnpm、ssh/scp、curl。

## 预览命令

```powershell
python scripts\deploy_sub2api_local.py --dry-run
```

## 编译并部署

```powershell
python scripts\deploy_sub2api_local.py
```

默认会执行：

1. 使用 pnpm 编译前端。
2. 在本机交叉编译 Linux 后端。
3. 打包运行文件。
4. 上传到远程服务器。
5. 在远程服务器构建 Docker 运行镜像。
6. 重启 `sub2api` 服务。
7. 执行健康检查。

## 测试 dry-run 输出

```powershell
python scripts\test_deploy_sub2api_local.py
```
