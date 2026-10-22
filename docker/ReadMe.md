# Docker 建置與執行

以下指令從專案根目錄執行。

```bash
VERSION=$(tr -d '\r\n' < VERSION)
docker build --build-arg APP_VERSION="$VERSION" -f docker/main.dockerfile -t "music-player-server:$VERSION" .
docker run -d --name music-player -p 8000:8000 \
  -v music-player-data:/app/data \
  -v "$PWD/config.yaml:/app/config.yaml:ro" \
  --restart unless-stopped "music-player-server:$VERSION"
```

或使用 Compose：

```bash
bash docker/compose.sh up -d --build
bash docker/compose.sh logs -f
bash docker/compose.sh down
```

開啟 http://localhost:8000，初始帳號 `super`，密碼 `admin`。請登入後修改密碼；修改會撤銷此帳號所有登入。

## 設定與保存資料

- `config.yaml` 決定容器內監聽 port、TLS、資料位置與上傳限制。若改為 9000，port mapping 也要改為 `9000:9000`。
- named volume 保存 SQLite 與實際音樂。不要使用 `down -v`，除非確定要刪除全部資料。
- 容器以 UID 10001 執行。若改用 bind mount，請先讓該 UID 有資料目錄讀寫權限。
- 設定改動後重新啟動容器。`MPLAYER_CONFIG` 可以指定另一個容器內 YAML 路徑。
- TLS：將憑證以唯讀方式掛載，如 `-v "$PWD/certs:/certs:ro"`，設定 `tls: true`、`certfile: /certs/fullchain.pem`、`keyfile: /certs/privkey.pem`，並將 `cookie_secure` 設為 `true`。TLS 也會自動啟用 Secure Cookie。
- 若 TLS 由反向代理終結，後端可維持 `tls: false`，但對外 HTTPS 時仍請設 `cookie_secure: true`。
- 備份時先停止服務，將整個 data volume（資料庫與音樂）一起備份，避免資料不一致。
- Gunicorn 固定單一 worker、8 threads，因檔案與資料庫的操作鎖為同一行程共享；請勿啟動多個 worker 或多個副本共用同一個 data。

健康檢查：`GET /health` 回傳 `{"status":"ok"}`。

## 版本管理

根目錄 `VERSION` 是唯一版號來源。更新時修改此檔案，再執行 `bash docker/compose.sh up -d --build`。腳本自動設定 `music-player-server:<版號>` image tag 與 `org.opencontainers.image.version` OCI 標籤；容器內站台也讀取同一份 VERSION。支援 `1.0.0`、`1.0.0-rc.1` 等格式。

只建置：`bash docker/compose.sh build`。檢查設定：`bash docker/compose.sh config`。請透過腳本執行 Compose。直接 docker build 時必須如上傳入 APP_VERSION，Dockerfile 會檢查它與 VERSION 相同。

若 WSL 的掛載目錄內出現 `docker-credential-desktop.exe: Invalid argument`，可切換到 WSL 本機目錄再執行 Docker。`compose.sh` 已自動切換工作目錄，專案與資料掛載仍依 Compose 檔案的絕對位置解析，不需更改憑證設定。

## 推送到私有倉庫

先建置，再推送；tag 一律讀取根目錄 `VERSION`：

```bash
bash docker/compose.sh build
bash docker/push.sh --dry-run
bash docker/push.sh
```

預設推送到 `as5304t-b8ed:5000/workspace/music-player-server:<版號>`。`--dry-run` 僅顯示來源、目的地與命令，不呼叫 Docker、不推送。腳本不會自動建置；正式推送前會確認本機 image 存在且 OCI 版本標籤符合 VERSION，失敗即停止。

每個人可以透過環境變數指定自己的倉庫：

```bash
REGISTRY=registry.example.com:5000 NAMESPACE=my-team IMAGE_NAME=music-player-server bash docker/push.sh
```

| 變數 | 預設值 | 用途 |
|---|---|---|
| `REGISTRY` | `as5304t-b8ed:5000` | 倉庫主機與選填 port，不含 http(s):// 或路徑 |
| `NAMESPACE` | `workspace` | 倉庫中的命名空間，可設空字串省略 |
| `IMAGE_NAME` | `music-player-server` | 遠端 image 名稱 |
| `LOCAL_IMAGE` | `music-player-server` | 已建置的本機 image 名稱，不含 tag |

例如不使用命名空間：`REGISTRY=registry.example.com NAMESPACE= bash docker/push.sh`。需要驗證的倉庫，請先在 WSL 本機目錄執行 `docker login <倉庫主機:port>`；腳本使用 Docker 現有登入資訊，不儲存帳密，也不修改 Docker daemon 的 TLS 或倉庫設定。

## Face ID／通行密鑰部署

1.2.0 新增 WebAuthn。預設停用，避免把內部主機名稱或錯誤網域當作驗證依據。先為站台設定可信任的 HTTPS 網域，再在掛載的 config.yaml 加入：

```yaml
passkeys:
  enabled: true
  rp_id: music.example.com
  origin: https://music.example.com
security:
  cookie_secure: true
```

請將範例換成實際網址。rp_id 只有主機名稱，不含 scheme、port、路徑；本專案要求它等於 origin 的主機名稱。origin 必須與瀏覽器網址的 origin 完全一致，不含路徑或結尾斜線；若對外使用非預設 port，需包含它，例如 `https://music.example.com:18443`。不可使用普通 HTTP 的 NAS IP 網址啟用 Face ID。更換網域後需重新綁定通行密鑰。

Nginx 可終結 TLS，Flask 後端維持 HTTP；驗證使用明確設定的 public origin，不信任任意 Host／X-Forwarded-Host 標頭。請確保代理保留 Origin、Authorization 與 Cookie，前端和 API 同源。若啟用的通行密鑰 origin 為 HTTPS，服務也會將音訊 Cookie 標記 Secure。

更新 requirements 或 Docker image 後重啟服務；舊版 YAML 沒有 passkeys 區段仍可正常使用密碼登入。每位使用者先用密碼登入，從左側「Face ID／通行密鑰設定」輸入目前密碼完成綁定，下次在登入頁輸入帳號並按「Face ID／通行密鑰登入」。實際驗證方式由裝置決定，可能是 Face ID、Touch ID、Windows Hello、PIN 或同步的通行密鑰。

localhost 開發測試可設 rp_id 為 `localhost`、origin 為 `http://localhost:8000`，並保持 cookie_secure 為 false。這項例外不適用於從手機存取電腦的區網 HTTP IP。
