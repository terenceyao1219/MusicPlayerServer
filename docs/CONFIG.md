# config.yaml 設定參考

以下對照目前 **1.4.2**。參數以 `區段.名稱` 表示。預設讀取專案根目錄的 `config.yaml`，環境變數 `MPLAYER_CONFIG` 可指定其他檔案。設定檔必須存在，修改後需重啟服務。

**未指定指省略欄位，不是填入 `null` 或空字串。範例值不等於程式預設值。** `server`、`storage`、`security`、`upload` 區段必須保留；`security` 可用 `{}`。`passkeys`、`hls` 可整段省略或使用 `{}`，不要只寫空的區段名稱（會被解析為 `null`）。布林值使用不加引號的 `true` / `false`。

適用版本指本專案版本。**1.2.1 起確認**表示最早 Git 快照已包含該參數，但無法確認首次加入的版號；其餘起始版本依更新紀錄標記。以下參數皆仍適用於目前版本。

## 服務與 TLS

| 參數 | 用途／格式 | 是否必填 | 未指定時 | 隨附範例值 | 適用版本 |
|---|---|---|---|---|---|
| `server.host` | 監聽位址；`0.0.0.0` 為所有 IPv4 介面，`127.0.0.1` 僅本機 | 是 | 無預設，啟動入口讀取時失敗 | `0.0.0.0` | 1.2.1 起確認 |
| `server.port` | TCP 監聽 port，使用整數；Docker 容器 port 對應也需一致 | 是 | 無預設，啟動入口讀取時失敗 | `8000` | 1.2.1 起確認 |
| `server.tls` | 是否由 Python／Gunicorn 直接提供 HTTPS | 否 | 停用 TLS | `false` | 1.2.1 起確認 |
| `server.certfile` | TLS 憑證檔案路徑 | TLS 啟用時必填 | 無預設；TLS 停用時不使用 | `null` | 1.2.1 起確認 |
| `server.keyfile` | TLS 私鑰檔案路徑 | TLS 啟用時必填 | 無預設；TLS 停用時不使用 | `null` | 1.2.1 起確認 |

TLS 路徑建議使用絕對路徑，相對路徑由程序工作目錄解析。反向代理提供 HTTPS 時，後端可保持 `server.tls: false`，並設定 `security.cookie_secure: true`。

## 儲存、安全與上傳

| 參數 | 用途／格式 | 是否必填 | 未指定時 | 隨附範例值 | 適用版本 |
|---|---|---|---|---|---|
| `storage.database` | SQLite 檔案路徑，保存帳號、授權、音樂索引等資料 | 是 | 無預設，啟動失敗 | `data/music.sqlite3` | 1.2.1 起確認 |
| `storage.music` | 實際音樂根目錄 | 是 | 無預設，啟動失敗 | `data/music` | 1.2.1 起確認 |
| `security.cookie_secure` | 將音訊 Cookie 標為 Secure，僅透過 HTTPS 傳送 | 否，但須保留 `security` 區段 | `false`；仍可能依下方條件啟用 | `false` | 1.2.1 起確認 |
| `upload.max_mb` | 每次請求上限，單位 MiB（1,048,576 bytes），包含多檔總和與 multipart 開銷；使用正整數 | 是 | 無預設，啟動失敗 | `200` | 1.2.1 起確認 |

儲存路徑相對於**專案根目錄**，亦可使用絕對路徑。資料庫父目錄與音樂目錄會自動建立，服務帳號須有讀寫權限。

音訊 Cookie 的 Secure 旗標在任一條件成立時啟用：`security.cookie_secure` 為 `true`、`server.tls` 為 `true`，或 `passkeys.origin` 以 `https://` 開頭。最後一項不依賴 `passkeys.enabled`，本機 HTTP 開發請勿留下 HTTPS origin。

## Face ID／通行密鑰

| 參數 | 用途／格式 | 是否必填 | 未指定時 | 隨附範例值 | 適用版本 |
|---|---|---|---|---|---|
| `passkeys.enabled` | 啟用 WebAuthn，仍需有效 RP ID 與 origin | 否 | `false` | `false` | 1.2.0 起 |
| `passkeys.rp_id` | 驗證網域，例如 `music.example.com`；不含協定、port 或路徑，須等於 origin 主機名稱 | 啟用通行密鑰時必填 | 空字串 `""`，功能不可用 | `""` | 1.2.0 起 |
| `passkeys.origin` | 瀏覽器實際 origin，例如 `https://music.example.com`；非預設 port 須包含，不可含路徑、結尾斜線、帳密、查詢或 fragment | 啟用通行密鑰時必填 | 空字串 `""`，功能不可用 | `""` | 1.2.0 起 |

正式環境須使用 HTTPS；本機例外為 `rp_id: localhost` 搭配 `origin: http://localhost:8000`。設定缺少或不符條件時通行密鑰不可用，密碼登入仍可使用。更換網域需重新綁定，範例見 [部署文件](../docker/ReadMe.md)。

## HLS 背景連播

| 參數 | 用途／格式 | 是否必填 | 未指定時 | 隨附範例值 | 適用版本 |
|---|---|---|---|---|---|
| `hls.enabled` | 啟用音樂庫 HLS 背景連播；原始串流可獨立使用 | 否 | `true` | `true` | 1.3.0 起 |
| `hls.ffmpeg` | 從 PATH 尋找的 FFmpeg 執行檔名稱，或執行檔路徑；自訂路徑建議使用絕對路徑 | 否 | `ffmpeg` | `ffmpeg` | 1.3.0 起；1.4.0 起亦供快捷 HLS 測試使用 |
| `hls.cache` | 轉碼快取目錄，相對於專案根目錄，亦可使用絕對路徑 | 否 | **資料庫所在目錄下的 `hls-cache`** | `data/hls-cache` | 1.3.0 起 |
| `hls.cache_mb` | 快取清理目標，單位 MiB；整數，最小採用 `32`；使用中的快取不刪除，因此不是磁碟硬上限 | 否 | `2048` | `2048` | 1.3.0 起 |
| `hls.idle_seconds` | 工作階段閒置回收秒數；整數，最小採用 `60`；playlist、segment、狀態請求會續期 | 否 | `3600` | `3600` | 1.3.0 起 |
| `hls.max_sessions` | 單一服務程序允許的 HLS 工作階段總數；整數，最小採用 `1` | 否 | `32` | `32` | 1.3.0 起 |
| `hls.ahead_seconds` | 預備後續音訊的目標秒數；整數，限制為 `0`–`1800`，超出時採用邊界值；仍至少預備三首、最多二十首，`0` 不會停用預備 | 否 | `300` | `300` | 1.3.4 起 |

若資料庫改為 `/srv/music/library.sqlite3` 並省略 `hls.cache`，快取在 `/srv/music/hls-cache`；若保留範例的 `data/hls-cache`，則仍位於專案根目錄下。

找不到 FFmpeg 時原始串流仍可用，但 HLS 無法轉碼。工作階段存在程序記憶體，重啟即失效，請維持單一服務程序。閒置清理在 API 請求時觸發。

1.4.0 起的「免登入快捷 HLS 播放測試」由各帳號在介面啟用，開關保存在 SQLite，沒有額外 YAML 參數。它共用 `hls.ffmpeg`，但不受 `hls.enabled` 或音樂庫 HLS session 配額控制；音訊存放於資料庫旁的 `quick-test-audio-v1`，不使用 `hls.cache`。

## 最小設定範例

以下保留必需區段與欄位，其餘使用程式預設值，因此 HLS 預設啟用、通行密鑰預設停用：

```yaml
server:
  host: 0.0.0.0
  port: 8000
storage:
  database: data/music.sqlite3
  music: data/music
security: {}
upload:
  max_mb: 200
```

完整範例見 [config.yaml](../config.yaml)，版本演進見 [更新紀錄](../CHANGELOG.md)。


### 自動掃描與播放方式
`hls.enabled: true` 時播放介面直接使用 HLS；`false` 時直接播放原始串流，不再顯示播放方式選單。啟用 HLS 時仍須安裝 FFmpeg。

管理員可在「進階設定 → 音樂庫同步」手動同步，或開啟自動掃描（預設關閉，設定存於 SQLite）。自動掃描限定 Linux，明確使用 watchdog 的 InotifyObserver，不採用檔案系統輪詢；啟用及伺服器重啟時會先同步一次，之後合併檔案關閉寫入、移動、刪除與新增資料夾事件後同步。新資料夾不會自動授權一般使用者。

音樂目錄必須能提供本機 Linux inotify 事件；NFS/SMB 遠端主機上的異動可能不會傳送事件，此時請使用手動同步。部署仍須維持單一 Gunicorn worker（現有預設）。
