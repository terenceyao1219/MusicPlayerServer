# Music Player Server WebAPI

## 目錄

- [通用規則與錯誤](#common)
- [認證與登入](#auth)
- [帳號管理](#users)
- [音樂資料夾](#folders)
- [資料夾權限](#permissions)
- [音樂管理](#tracks)
- [掃描並同步音樂庫](#sync)
- [播放器與串流](#playback)
- [健康檢查與前端](#health)
- [呼叫範例](#example)

<a id="common"></a>
## 通用規則與錯誤

API 基底 `/api/v1/mplayer`，本文路徑皆相對於此基底。除 login、version、passkeys/status 與 passkeys/login/* 外需 `Authorization: Bearer <access_token>`。只有 `GET /tracks/{id}/stream` 可改用登入時設定的音訊 Cookie。所有成功 JSON 回應預設 200；建立資源為 201。上傳採 multipart，其餘寫入採 `Content-Type: application/json`。DELETE 不需要 body。

錯誤格式：`{"error":"錯誤說明"}`。

| HTTP | 說明 |
|---|---|
|400|JSON、ID、名稱、格式、密碼、播放位置或模式不正確|
|401|缺少認證、登入失敗、Token 過期或撤銷|
|403|角色不足或沒有資料夾權限|
|404|帳號、資料夾、歌曲、檔案或可播放曲目不存在|
|409|名稱衝突、非空資料夾刪除、最後一位 admin 移除|
|413|超過設定的請求大小限制|
|416|音訊 Range 超出檔案範圍|
|500|檔案操作失敗（伺服器記錄詳細錯誤）|

ID 是正整數；批量 ID 陣列限 1–1000 筆，重複 ID 自動去重。檔案/資料夾名稱 1–160 字元，不可含 `/ \ < > : " | ? *`、控制字元、前後空白、結尾句點或 `.` / `..`。改名保留音樂格式副檔名。無分頁，列表回傳完整可見集合。

<a id="auth"></a>
## 認證與登入

| 方法與路徑 | 權限 | 請求 / 回應 |
|---|---|---|
|POST `/login`|公開|body: `{"username":"super","password":"admin"}`；回傳 `{"access_token":"…","token_type":"Bearer","expires_in":86400,"user":{"id":1,"username":"super","role":"admin"}}`|
|GET `/me`|登入|回傳 `{"id":1,"username":"super","role":"admin"}`|
|POST `/logout`|登入|無 body；撤銷目前 Token、清除音訊 Cookie，回傳 `{"ok":true}`|

Token 是伺服器保存雜湊的隨機 opaque token，固定有效 24 小時，不滑動延長，沒有 refresh token。登入另設 HttpOnly、SameSite=Strict、Path 為 `/api/v1/mplayer/tracks/` 的 Cookie（後端只在串流 GET/HEAD 接受它）；TLS 時使用 Secure。Cookie 不授權其他 API。密碼或角色修改會撤銷該帳號全部 Token。

<a id="users"></a>
## 帳號管理

下列端點均限 admin。

| 方法與路徑 | 請求 | 回應 |
|---|---|---|
|GET `/users`|無|`[{"id":1,"username":"super","role":"admin"}]`|
|POST `/users`|`{"username":"alice","password":"password123","role":"user"}`|201 `{"id":2}`|
|PATCH `/users/{id}`|可提供 `username`、`password`、`role` 的任意組合|`{"ok":true}`|
|DELETE `/users/{id}`|無|`{"ok":true}`|

密碼 8–128 字元；role 限 admin/user，建立預設 user。PATCH 不改密碼請省略 password，不可傳空字串。不可刪除自己，不可刪除或降級最後一位 admin。刪除帳號會同時刪除其 session 與授權；瀏覽器本機播放紀錄不會由伺服器遠端刪除。編輯自己的帳號後需要重新登入。

<a id="folders"></a>
## 音樂資料夾

| 方法與路徑 | 權限 | 請求 / 回應 |
|---|---|---|
|GET `/folders`|登入|`[{"id":1,"name":"Jazz","track_count":3}]`，user 僅取得授權資料夾|
|POST `/folders`|admin|body `{"name":"Jazz"}`；201 `{"id":1}`|
|PATCH `/folders/{id}`|admin|body `{"name":"Night Jazz"}`；`{"ok":true}`|
|DELETE `/folders/{id}`|admin|僅能刪空資料夾；`{"ok":true}`|

對應實際目錄為 `<storage.music>/<name>/`，不接受子資料夾。資料夾改名會同步改實際目錄。名稱衝突為 409。

<a id="permissions"></a>
## 資料夾權限

管理介面採「先選 user，再勾選資料夾」方式。已授權的資料夾預先勾選，一次儲存該使用者的完整授權清單。

| 方法與路徑 | 權限 | 請求 / 回應 |
|---|---|---|
|GET `/users/{id}/permissions`|admin|`{"folder_ids":[1,3]}`，取得指定 user 已授權的資料夾 ID|
|PUT `/users/{id}/permissions`|admin|body `{"folder_ids":[1,3]}`；回傳更新後的 `folder_ids`|

PUT 完整取代**該 user** 的授權，不影響其他使用者；`[]` 撤銷其全部資料夾權限。僅接受既存 user（admin 回傳 400、不存在回傳 404）。所有資料夾先驗證存在再寫入，任一 ID 不合法或不存在時不變更原有授權。ID 陣列最多 1000 筆，重複 ID 去重；回傳依 ID 排序。既有授權資料無需遷移。

以下資料夾導向端點保留相容性，前端管理介面已改用上方 user 導向 API：

| 方法與路徑 | 權限 | 請求 / 回應 |
|---|---|---|
|GET `/folders/{id}/permissions`|admin|`{"user_ids":[2,3]}`|
|PUT `/folders/{id}/permissions`|admin|body `{"user_ids":[2,3]}`；回傳相同格式|

PUT 完整取代既有授權；空陣列撤銷全部 user 權限。每個 ID 必須為既存 user；admin 固有權限不需授權。歌曲清單、選曲、下一首與串流皆檢查權限；搬移歌曲後以目的地資料夾權限為準。

<a id="tracks"></a>
## 音樂管理

| 方法與路徑 | 權限 | 請求 / 回應 |
|---|---|---|
|GET `/tracks`|登入|可選 query `folder_id=1`；`[{"id":1,"name":"song.mp3","folder_id":1,"folder_name":"Jazz"}]`|
|POST `/folders/{id}/tracks`|admin|multipart 欄位 `files`，可重複多個；201 `[{"id":1,"name":"song.mp3"}]`|
|PATCH `/tracks/{id}`|admin|`{"name":"new-name.mp3"}`；`{"ok":true}`|
|POST `/tracks/move`|admin|`{"track_ids":[1,2],"folder_id":3}`；`{"ok":true,"count":2}`|
|POST `/tracks/delete`|admin|`{"track_ids":[1,2]}`；`{"ok":true,"count":2}`|

支援 mp3/m4a/wav/ogg（副檔名大小寫皆可），拒絕空檔案。不做轉碼或內容解碼驗證。上傳大小是整個 multipart 請求合計，預設 200 MiB。同名檔案不覆寫；批量上傳/搬移遇衝突整批拒絕。搬移已在目的地的歌曲為 no-op，不計入 count。所有操作同步實際檔案與資料庫。刪除為永久刪除，前端呼叫前顯示警告確認。

列表依資料夾名稱、歌曲檔名、ID 排序；播放器相同。GET `/tracks` 不帶 folder_id 仍只回傳可見歌曲。

<a id="sync"></a>
## 掃描並同步音樂庫

`POST /library/sync`，僅 admin，需 Bearer Token，無 body。同步執行完成後回傳 200：

```json
{
  "folders_added": 2,
  "folders_removed": 1,
  "tracks_added": 8,
  "tracks_removed": 3,
  "skipped_count": 1,
  "skipped": [{"path": "Jazz/notes.txt", "reason": "不支援的音樂格式"}]
}
```

- 以 `storage.music` 當前檔案清單更新 DB，只掃描一層實體資料夾內 mp3/m4a/wav/ogg（副檔名不分大小寫）。空資料夾也登錄。
- 新資料夾不產生任何 user 授權；同路徑既有資料夾保留 ID 與授權，同路徑曲目保留 ID，因此本機續播紀錄仍可使用。
- DB 中不存在於有效掃描清單的歌曲、資料夾會移除。刪除資料夾紀錄也會清除其授權；本機紀錄指向已刪曲目時，恢復 API 將 track_id 設為 null、position 歸零。
- 外部改名／搬移視為舊路徑刪除、新路徑新增，不推測檔案身份，不繼承舊授權或進度。
- 略過根目錄檔案、巢狀資料夾（不遞迴）、符號連結、非一般檔案、空檔案、不支援格式及不符合名稱規則的項目；`skipped` 最多列出 100 筆，`skipped_count` 為完整略過數。
- 完整掃描成功後才在同一 SQLite 交易中寫入；目錄不存在或讀取失敗回傳 500，DB 保持原狀。掃描不寫入、搬移或刪除實際檔案。
- 可重複執行，未變動時新增／移除數均為 0。請完成外部檔案操作後再掃描，外部程序不受 API 操作鎖保護。
- 同步為手動觸發；大量檔案需等待，部署時應依規模調整 HTTP timeout。掛載磁碟若呈現空目錄會視為已清空，請先確認儲存可用。

<a id="playback"></a>
## 播放器與串流

播放器 API 不保存帳號共用狀態。每個視窗提供自己的歌曲與模式，互不影響。

| 方法與路徑 | 請求 / 回應 |
|---|---|
|GET `/tracks/{id}/stream`|明確指定歌曲；Bearer 或音訊 Cookie；200 / 206 音訊|
|POST `/playback/resolve`|提供 track_id、mode、direction、trigger；回傳目標 track_id 與 stream_url|
|POST `/playback/state`|驗證並標準化狀態 JSON；回傳 state、stream_url、reason，不寫入播放狀態|
|POST `/playback/restore`|驗證瀏覽器提交的狀態；無效歌曲清空，回傳可恢復狀態，不寫入播放狀態|

除串流外均須 Bearer。完整狀態格式（省略欄位採下列預設值，不做部分合併）：

```json
{"schema_version":1,"track_id":null,"position":0,"mode":"folder"}
```

schema_version 只接受整數 1；track_id 為正整數或 null；position 為非負有限秒數；mode 為 single/folder/global/random。無歌曲時 position 標準化為 0。格式錯誤回傳 400。

例如 POST `/playback/restore` body：

```json
{"schema_version":1,"track_id":123,"position":85.5,"mode":"folder"}
```

成功回應：

```json
{"state":{"schema_version":1,"track_id":123,"position":85.5,"mode":"folder"},"stream_url":"/api/v1/mplayer/tracks/123/stream","reason":null}
```

- `/state`：歌曲不存在／實體檔案遺失為 404，無權限為 403。
- `/restore`：上述情況回傳 200，state.track_id=null、position=0，保留合法 mode，stream_url=null、reason="unavailable"。401 與其他服務錯誤不會被當成空狀態。
- 恢復 API 不啟動播放；前端載入 metadata 後設定秒數，超過歌曲長度時限制至長度，再由使用者按播放。

上一首／下一首範例：

```json
{"track_id":123,"mode":"folder","direction":1,"trigger":"manual"}
```

回應：`{"track_id":124,"stream_url":"/api/v1/mplayer/tracks/124/stream"}`。

- direction 為 1（預設，下一首）或 -1（上一首）；trigger 為 manual（預設）或 ended。
- mode 預設 folder；folder 在目前歌曲資料夾循環，global 在所有授權歌曲循環。
- random 從所有授權歌曲隨機選曲，有其他歌曲時排除目前歌曲；只有一首時重播。API 的 direction=1/-1 都回傳隨機候選，不保存歷史；網頁播放器自行保留本視窗最近 200 筆隨機歷史，上一首優先回到歷史歌曲，下一首優先使用前進歷史。重載頁面、選歌或改模式會清除歷史，無歷史時使用 API 隨機候選。
- single + ended 重播原曲；single + manual 在目前資料夾切換上一首／下一首。
- 排序依資料夾名稱、檔名、ID；首尾循環。track_id=null 或省略時，下一首選全部授權歌曲第一首，上一首選最後一首。
- 目前歌曲不存在／檔案遺失回傳 404，無權限回傳 403；無可選歌曲為 404。目標歌曲在串流時仍會重新檢查權限與檔案。
- resolve 僅計算目標，不更改任何播放器或帳號狀態；GET 串流也絕不換歌。

瀏覽器保存方式：

- localStorage key 為 `mplayer:playback:v1:<user_id>`，只保存完整狀態 JSON，不保存 Token。
- 每 5 秒、暫停、跳轉、換歌、模式變更、登出及離頁時在本機保存；週期儲存不呼叫後端。
- 同瀏覽器設定檔、同 origin、同帳號視窗共用續播紀錄，最後寫入為準；不將其他視窗的 storage 變更套用到正在播放的音訊。不同裝置／瀏覽器各自記憶。
- 儲存遭封鎖／容量不足時提示，仍可在本視窗播放；清除網站資料會失去續播紀錄。損壞或不支援版本的紀錄重設為預設狀態。

串流支援 mp3 audio/mpeg、m4a audio/mp4、wav audio/wav、ogg audio/ogg。支援單一 byte Range、Content-Range、seek，超界 416，Cache-Control: no-store。每次 GET/HEAD 重新驗證權限；已下載音訊無法追回。Cookie 用於原生 audio，其他客戶端可直接使用 Bearer。

### 舊版遷移

舊 GET/PUT `/playback`、POST `/playback/next`、GET `/playback/stream` 已移除（帶有效認證回傳 404）。舊 DB playback 表保留但不再用於播放，也不自動匯入本機紀錄。升級後第一次登入從空狀態開始，既有登入請重新登入以取得新 Cookie 路徑。新增 id_counters 表記錄帳號／歌曲 ID 高水位，保留既有資料表與 ID，之後由 API 新增的資料不重用已刪除 ID，避免本機舊紀錄指向新資料。

<a id="health"></a>
## 健康檢查與前端

以下路徑**不含 API 基底**，無需認證：

| 路徑 | 說明 |
|---|---|
|GET `/health`|`{"status":"ok"}`，同時查詢 SQLite|
|GET `/`|前端登入 / 應用程式 HTML|
|GET `/assets/{filename}`|前端靜態資源，不包含音樂儲存目錄|

<a id="example"></a>
## 呼叫範例

```bash
# 登入並取得 access_token
curl -X POST http://localhost:8000/api/v1/mplayer/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"super","password":"admin"}'

# 將回傳 Token 填入本機變數
TOKEN='貼上 access_token'
curl -X POST http://localhost:8000/api/v1/mplayer/folders \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Jazz"}'

curl -X POST http://localhost:8000/api/v1/mplayer/folders/1/tracks \
  -H "Authorization: Bearer $TOKEN" -F 'files=@song.mp3'

curl -X POST http://localhost:8000/api/v1/mplayer/playback/state \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"track_id":1,"mode":"folder","position":0}'

curl http://localhost:8000/api/v1/mplayer/tracks/1/stream \
  -H "Authorization: Bearer $TOKEN" -H 'Range: bytes=0-1023' \
  --output first-1024-bytes.bin
```

## 站台版本

`GET /api/v1/mplayer/version` 不需登入，回傳 `{"version":"1.0.0"}`（實際值由根目錄 VERSION 讀取），使用 no-store 避免快取舊版號。

## Face ID／通行密鑰（1.2.0）

使用 WebAuthn，Face ID、Touch ID、Windows Hello 或裝置 PIN 由瀏覽器／作業系統決定，網站不能強制只用 Face ID，也不取得人臉或指紋資料。原本密碼登入保留。

| 方法與路徑 | 認證及用途 |
|---|---|
| GET `/passkeys/status` | 公開；回傳 enabled 與 origin（停用時 null） |
| GET `/passkeys` | Bearer；列出自己綁定的 id/name/created/last_used |
| DELETE `/passkeys/{id}` | Bearer；僅能移除自己的通行密鑰，成功回傳 ok:true，不登出既有 session |
| POST `/passkeys/register/options` | Bearer＋Origin；body 為 name（1–80 字元，預設「我的通行密鑰」）、password（目前密碼）；最多 20 組 |
| POST `/passkeys/register/verify` | Bearer＋Origin；body 為 challenge_id 與 credential；成功 201 ok:true |
| POST `/passkeys/login/options` | 公開但需 Origin；body 為 username |
| POST `/passkeys/login/verify` | 公開但需 Origin；body 為 challenge_id 與 credential；成功回傳與密碼登入相同的 access_token/user 等欄位及音訊 Cookie |

options 端點回傳 `{"challenge_id":"...","options":{...}}`。options 是 WebAuthn JSON，前端將 challenge、user.id、credential ID 從 base64url 轉成 ArrayBuffer，再交給 navigator.credentials.create/get。verify 的 credential 是瀏覽器結果，二進位欄位以 base64url 編碼，包含 id/rawId/type/response/clientExtensionResults。

challenge 有效 120 秒、只能使用一次；每個帳號每種流程只保留最新請求（另一視窗重新開始流程會使先前請求失效）。註冊請求還綁定目前 Bearer session。伺服器檢查 challenge、RP ID、精確 origin、使用者驗證旗標、簽章與簽章計數；不接受僅傳送「驗證成功」的布林值。登入與綁定請求最多 64 KiB。錯誤回傳 400（格式／驗證／過期）、401（未登入）、403（Origin／密碼／擁有者不符）、409（重複憑證或數量上限）、503（尚未設定）。

新增 passkeys 與 passkey_challenges 資料表，不刪除現有帳號或資料。修改帳號會清除未完成 challenge 與登入 session，但保留已綁定通行密鑰；不再需要的密鑰請在設定中移除。刪除帳號則連同密鑰及 challenge 一併刪除。公鑰與憑證 ID 保存在 DB，私鑰由裝置／密鑰管理器保管。
