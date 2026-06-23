Write-Host "=== 构建进度检查 ===" -ForegroundColor Cyan
Write-Host "运行中，请稍候..." -ForegroundColor Yellow
$key = "C:\Users\31950\Downloads\腾讯云服务器秘钥.pem"
$result = ssh -i $key -o ConnectTimeout=10 ubuntu@129.204.166.180 "
  echo '--- 构建日志最后5行 ---'
  tail -5 ~/build_v5.log 2>/dev/null
  echo ''
  echo '--- 已安装包 ---'
  ls ~/.platformio/packages/ 2>/dev/null
  echo ''
  echo '--- 下载缓存 ---'
  du -sh ~/.platformio/.cache/downloads/ 2>/dev/null
  echo ''
  echo '--- 进程 ---'
  ps aux | grep 'platformio run' | grep -v grep | awk '{print \$2, \$10, \$11}'
  echo ''
  tmux has-session -t pio_build 2>/dev/null && echo 'TMUX: running' || echo 'TMUX: finished'
"
Write-Host $result
