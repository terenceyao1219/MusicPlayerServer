# Music Player Server WebAPI

本文件依實際後端實作整理。每支端點分別列出 Purpose、HTTP Method、End-point、HTTP Header、URL Param、Body 與 Response。範例中的 ID、Token、時間與憑證均為示意，請使用前一個 API 的實際回傳值。

## 目錄

- [通用規則](#common)
- [登入與版本](#auth)
- [帳號管理](#users)
- [音樂資料夾](#folders)
- [資料夾權限](#permissions)
- [音樂管理](#tracks)
- [掃描同步](#sync)
- [播放器與串流](#playback)
- [HLS 背景連播](#hls)
- [Face ID／通行密鑰](#passkeys)
- [健康檢查與靜態資源](#health)
- [完整呼叫範例](#example)
- [同步、播放器與遷移補充](#notes)

<a id="common"></a>
## 通用規則

| 項目 | 說明 |
|---|---|
| `{{API-Prefix}}` | 站台 origin 加上 `/api/v1/mplayer`，例如 `https://music.example.com:18443/api/v1/mplayer`；最後不加 `/` |
| `{{Site-Origin}}` | 站台協定、主機與連接埠，例如 `https://music.example.com:18443` |
| 公開 | 不需要 Token |
| 登入 | admin 或 user，需 `Authorization: Bearer <access_token>` |
| admin | 已登入且角色為 admin；user 呼叫回傳 403 |
| JSON | 請求 Body 必須為 JSON object；成功回應依各端點直接回傳 object 或 array，不包裝 `code/message/data` |
| HTTP 狀態 | 狀態碼位於 HTTP response status，不是 JSON 的 `code` 欄位 |
| Query | 除 `/tracks` 的 `folder_id` 外，目前沒有查詢參數；不支援分頁、關鍵字篩選 |
| 快取 | API 回應使用 `Cache-Control: no-store` |

### 認證與 Cookie

Token 有效 86400 秒（24 小時），不滑動延長，沒有 refresh token。伺服器僅保存 Token 雜湊。密碼登入與通行密鑰登入都會設定 `mplayer_media` Cookie：HttpOnly、SameSite=Strict、Path=/api/v1/mplayer/tracks/。`security.cookie_secure`、`server.tls` 或 HTTPS passkeys origin 任一符合時使用 Secure。

只有歌曲串流與 HLS playlist／segment 的 GET/HEAD 可使用此 Cookie；其他受保護 API 仍須 Bearer。若串流同時帶 Bearer 與 Cookie，優先使用 Bearer。修改帳號會撤銷該帳號全部 session；登出僅撤銷目前 Token。

### 共用欄位限制

| 欄位類型 | 限制 |
|---|---|
| JSON ID | 正整數，不接受字串或 boolean |
| ID 陣列 | 一般批次操作 1–1000 筆，重複 ID 去重；權限更新另外允許空陣列 |
| 名稱 | 帳號、資料夾、檔名皆為 1–160 字元；不可有前後空白、控制字元、結尾句點、`.`、`..` 或 `/ \ < > : " &#124; ? *` |
| 密碼 | 建立／修改帳號時為 8–128 字元；初始化空資料庫的預設帳號 `super` / `admin` 為初始化例外，登入不套用建立密碼的長度限制 |
| 音樂格式 | mp3、m4a、wav、ogg，副檔名不分大小寫；API 檔名包含副檔名 |

### 錯誤 Response

```json
{
  "error": "請重新登入，認證已失效或過期"
}
```

| HTTP Status | 說明 |
|---|---|
| 400 | Body、欄位值或 WebAuthn 驗證不正確 |
| 401 | 未登入、登入失敗、Token 過期或撤銷 |
| 403 | 權限不足、Origin 不符，或綁定密鑰時目前密碼錯誤 |
| 404 | 資源／實體檔案不存在、無可選曲目 |
| 405 | HTTP Method 不支援 |
| 409 | 名稱／憑證衝突、最後一位 admin 保護、通行密鑰達上限 |
| 413 | 請求資料超出大小限制 |
| 416 | 音訊 Range 無法滿足 |
| 500 | 伺服器或檔案操作失敗 |
| 503 | 通行密鑰未完成設定或未啟用 |

以下各節列出該端點主要錯誤；受保護端點皆可能回傳 401，admin 端點另可能回傳 403。由 Nginx 等反向代理產生的錯誤，內容格式可能不同。


<a id="auth"></a>
## 登入與版本


### 帳號密碼登入

| 項目 | 說明 |
|---|---|
| Purpose | 驗證帳密並建立登入 session。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/login` |
| 權限 | 公開 |
| HTTP Header | `Content-Type: application/json` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

```json
{
  "username": "super",
  "password": "admin"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| username | string | 是 | 帳號名稱 |
| password | string | 是 | 目前密碼 |

**Response — HTTP 200**

```json
{
  "access_token": "<access_token>",
  "token_type": "Bearer",
  "expires_in": 86400,
  "user": {
    "id": 1,
    "username": "super",
    "role": "admin"
  }
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| access_token | string | 後續 API 使用的 Token |
| token_type | string | 固定 Bearer |
| expires_in | integer | 有效秒數，固定 86400 |
| user.id | integer | 登入帳號 ID |
| user.username | string | 帳號名稱 |
| user.role | string | admin 或 user |

**行為說明**

成功同時設定音訊 Cookie。預設帳密只在空帳號資料庫初始化；重啟不會重設既有密碼。

**主要錯誤**

400：缺少字串型別的帳號或密碼；401：帳號或密碼錯誤。

### 取得目前帳號

| 項目 | 說明 |
|---|---|
| Purpose | 取得目前 Token 對應的帳號。 |
| HTTP Method | `GET` |
| End-point | `{{API-Prefix}}/me` |
| 權限 | 登入 |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
{
  "id": 1,
  "username": "super",
  "role": "admin"
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | integer | 登入帳號 ID |
| username | string | 帳號名稱 |
| role | string | admin 或 user |

### 登出

| 項目 | 說明 |
|---|---|
| Purpose | 撤銷目前 Token，清除音訊 Cookie。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/logout` |
| 權限 | 登入 |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
{
  "ok": true
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| ok | boolean | 成功時為 true |

**行為說明**

不撤銷其他裝置 session，也不刪除瀏覽器本機帳號字串或播放紀錄。

### 取得站台版本

| 項目 | 說明 |
|---|---|
| Purpose | 讀取服務啟動時載入的 VERSION。 |
| HTTP Method | `GET` |
| End-point | `{{API-Prefix}}/version` |
| 權限 | 公開 |
| HTTP Header | 無必要的應用程式標頭 |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
{
  "version": "1.3.1"
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| version | string | 實際版本以根目錄 VERSION 為準 |

<a id="users"></a>
## 帳號管理


### 列出帳號

| 項目 | 說明 |
|---|---|
| Purpose | 列出全部帳號，依 ID 排序。 |
| HTTP Method | `GET` |
| End-point | `{{API-Prefix}}/users` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
[
  {
    "id": 1,
    "username": "super",
    "role": "admin"
  }
]
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| [] | array | 無分頁 |
| [].id | integer | 帳號 ID |
| [].username | string | 帳號名稱 |
| [].role | string | admin 或 user |

### 新增帳號

| 項目 | 說明 |
|---|---|
| Purpose | 建立 admin 或 user 帳號。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/users` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

```json
{
  "username": "alice",
  "password": "password123",
  "role": "user"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| username | string | 是 | 唯一帳號名稱，符合共用名稱限制 |
| password | string | 是 | 8–128 字元 |
| role | string | 否 | admin 或 user；省略為 user |

**Response — HTTP 201**

```json
{
  "id": 2
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | integer | 新帳號 ID |

**主要錯誤**

400：名稱、密碼或角色不合法；409：帳號名稱重複。

### 修改帳號

| 項目 | 說明 |
|---|---|
| Purpose | 修改指定帳號並撤銷其全部登入。 |
| HTTP Method | `PATCH` |
| End-point | `{{API-Prefix}}/users/{id}` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| id | Path | integer | 是 | 帳號 ID，正整數 |

**Body**

```json
{
  "username": "alice",
  "password": "changed123",
  "role": "user"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| username | string | 否 | 唯一帳號名稱，符合共用名稱限制；省略保留原值 |
| password | string | 否 | 8–128 字元；省略保留原值 |
| role | string | 否 | admin 或 user；省略為 user；省略保留原值 |

**Response — HTTP 200**

```json
{
  "ok": true
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| ok | boolean | 成功時為 true |

**行為說明**

可只送要修改的欄位。password 省略或 null 都保留原密碼，空字串不合法。即使送空物件也會撤銷 session 與未完成的通行密鑰 challenge；已綁定密鑰保留。修改自己後需重新登入。

**主要錯誤**

400：欄位不合法；404：帳號不存在；409：名稱重複或降級最後一位 admin。

### 刪除帳號

| 項目 | 說明 |
|---|---|
| Purpose | 永久刪除指定帳號。 |
| HTTP Method | `DELETE` |
| End-point | `{{API-Prefix}}/users/{id}` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| id | Path | integer | 是 | 帳號 ID，正整數 |

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
{
  "ok": true
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| ok | boolean | 成功時為 true |

**行為說明**

同時移除 session、資料夾授權、通行密鑰及 challenge；不刪除音樂。瀏覽器本機播放紀錄不會被遠端清除。

**主要錯誤**

404：帳號不存在；409：最後一位 admin，或刪除目前登入帳號。

<a id="folders"></a>
## 音樂資料夾


### 列出資料夾

| 項目 | 說明 |
|---|---|
| Purpose | 取得目前帳號可見的資料夾，依名稱排序。 |
| HTTP Method | `GET` |
| End-point | `{{API-Prefix}}/folders` |
| 權限 | 登入 |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
[
  {
    "id": 1,
    "name": "Jazz",
    "track_count": 3
  }
]
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| [].id | integer | 資料夾 ID |
| [].name | string | 資料夾名稱 |
| [].track_count | integer | 資料庫登錄歌曲數 |

**行為說明**

admin 可見全部；user 僅可見已授權資料夾。無資料時回傳 []。

### 新增資料夾

| 項目 | 說明 |
|---|---|
| Purpose | 同步建立／修改資料庫與音樂儲存目錄。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/folders` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

```json
{
  "name": "Jazz"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| name | string | 是 | 符合共用名稱限制；只接受一層資料夾 |

**Response — HTTP 201**

```json
{
  "id": 1
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | integer | 新資料夾 ID |

**行為說明**

實體位置為 <storage.music>/<name>/。新增後不會自動授權 user。

**主要錯誤**

400：名稱不合法；409：資料夾已存在；500：檔案操作失敗。

### 重新命名資料夾

| 項目 | 說明 |
|---|---|
| Purpose | 同步建立／修改資料庫與音樂儲存目錄。 |
| HTTP Method | `PATCH` |
| End-point | `{{API-Prefix}}/folders/{id}` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| id | Path | integer | 是 | 資料夾 ID，正整數 |

**Body**

```json
{
  "name": "Jazz"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| name | string | 是 | 符合共用名稱限制；只接受一層資料夾 |

**Response — HTTP 200**

```json
{
  "ok": true
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| ok | boolean | 成功時為 true |

**行為說明**

實體位置為 <storage.music>/<name>/。新增後不會自動授權 user。

**主要錯誤**

400：名稱不合法；409：資料夾已存在；500：檔案操作失敗。404：資料夾不存在。

### 刪除資料夾

| 項目 | 說明 |
|---|---|
| Purpose | 刪除空資料夾與其登錄、授權。 |
| HTTP Method | `DELETE` |
| End-point | `{{API-Prefix}}/folders/{id}` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| id | Path | integer | 是 | 資料夾 ID，正整數 |

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
{
  "ok": true
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| ok | boolean | 成功時為 true |

**行為說明**

需先刪除或搬移歌曲；未登錄的實體檔案也會使目錄無法刪除。

**主要錯誤**

404：不存在；409：資料庫仍有歌曲；500：實體目錄非空或檔案操作失敗。

<a id="permissions"></a>
## 資料夾權限

建議管理介面使用帳號導向端點：先選 user，再一次提交完整資料夾清單。admin 自帶所有資料夾權限，不需授權。

### 取得帳號的授權清單

| 項目 | 說明 |
|---|---|
| Purpose | 讀取指定帳號的授權。 |
| HTTP Method | `GET` |
| End-point | `{{API-Prefix}}/users/{id}/permissions` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| id | Path | integer | 是 | 帳號 ID，正整數 |

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
{
  "folder_ids": [
    1,
    3
  ]
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| folder_ids | integer[] | 目前已授權的資料夾 ID |

**行為說明**

此 user 的回傳 ID 依數值排序。

**主要錯誤**

404：指定資源不存在。400：帳號不是 user。

### 取代帳號的授權清單

| 項目 | 說明 |
|---|---|
| Purpose | 完整取代指定帳號的授權。 |
| HTTP Method | `PUT` |
| End-point | `{{API-Prefix}}/users/{id}/permissions` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| id | Path | integer | 是 | 帳號 ID，正整數 |

**Body**

```json
{
  "folder_ids": [
    1,
    3
  ]
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| folder_ids | integer[] | 是 | 0–1000 個既存資料夾 ID；重複去重，[] 撤銷全部 |

**Response — HTTP 200**

```json
{
  "folder_ids": [
    1,
    3
  ]
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| folder_ids | integer[] | 目前已授權的資料夾 ID |

**行為說明**

此 user 的回傳 ID 依數值排序。先驗證所有 ID，再取代這個對象的完整授權；不會修改其他對象的授權。

**主要錯誤**

404：指定資源不存在。400：帳號不是 user。400：陣列或 ID 不合法，或授權對象是 admin；404：陣列內資源不存在。

### 取得資料夾的授權清單

| 項目 | 說明 |
|---|---|
| Purpose | 讀取指定資料夾的授權。 |
| HTTP Method | `GET` |
| End-point | `{{API-Prefix}}/folders/{id}/permissions` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| id | Path | integer | 是 | 資料夾 ID，正整數 |

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
{
  "user_ids": [
    2,
    3
  ]
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| user_ids | integer[] | 目前已授權的user 帳號 ID |

**行為說明**

保留相容性的資料夾導向端點；回傳陣列順序不應作為契約。

**主要錯誤**

404：指定資源不存在。

### 取代資料夾的授權清單

| 項目 | 說明 |
|---|---|
| Purpose | 完整取代指定資料夾的授權。 |
| HTTP Method | `PUT` |
| End-point | `{{API-Prefix}}/folders/{id}/permissions` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| id | Path | integer | 是 | 資料夾 ID，正整數 |

**Body**

```json
{
  "user_ids": [
    2,
    3
  ]
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| user_ids | integer[] | 是 | 0–1000 個既存user 帳號 ID；重複去重，[] 撤銷全部 |

**Response — HTTP 200**

```json
{
  "user_ids": [
    2,
    3
  ]
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| user_ids | integer[] | 目前已授權的user 帳號 ID |

**行為說明**

保留相容性的資料夾導向端點；回傳陣列順序不應作為契約。先驗證所有 ID，再取代這個對象的完整授權；不會修改其他對象的授權。

**主要錯誤**

404：指定資源不存在。400：陣列或 ID 不合法，或授權對象是 admin；404：陣列內資源不存在。

<a id="tracks"></a>
## 音樂管理


### 列出歌曲

| 項目 | 說明 |
|---|---|
| Purpose | 列出目前帳號可見歌曲。 |
| HTTP Method | `GET` |
| End-point | `{{API-Prefix}}/tracks` |
| 權限 | 登入 |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| folder_id | Query | integer | 否 | 只列指定資料夾；省略列全部可見歌曲 |

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
[
  {
    "id": 1,
    "name": "song.mp3",
    "folder_id": 1,
    "folder_name": "Jazz"
  }
]
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| [].id | integer | 歌曲 ID |
| [].name | string | 完整檔名，含副檔名 |
| [].folder_id | integer | 所屬資料夾 ID |
| [].folder_name | string | 所屬資料夾名稱 |

**行為說明**

例如 /tracks?folder_id=1。依資料夾名稱、檔名、歌曲 ID 排序。無歌曲回傳 []。目前實作對無法轉成整數的 folder_id 視為未提供；請傳正整數。admin 查不存在的資料夾回傳 []，user 沒有該資料夾授權則回傳 403。

**主要錯誤**

403：user 沒有指定資料夾的權限。

### 上傳歌曲

| 項目 | 說明 |
|---|---|
| Purpose | 上傳一個或多個音樂檔案並登錄歌曲。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/folders/{id}/tracks` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: multipart/form-data; boundary=...`（交由瀏覽器或 curl 產生） |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| id | Path | integer | 是 | 目的資料夾 ID，正整數 |

**Body**

```bash
curl -X POST "$API/folders/1/tracks" \
  -H "Authorization: Bearer $TOKEN" \
  -F "files=@song.mp3" -F "files=@second.wav"
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| files | file（可重複） | 是 | 每個檔案使用相同欄位名稱 files；檔名含副檔名 |

**Response — HTTP 201**

```json
[
  {
    "id": 1,
    "name": "song.mp3"
  }
]
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| [].id | integer | 新歌曲 ID |
| [].name | string | 儲存的完整檔名 |

**行為說明**

整個 multipart 請求預設最多 200 MiB，由 upload.max_mb 設定；不做轉碼或內容解碼驗證。同名不覆寫，批次衝突整批拒絕。

**主要錯誤**

400：缺少 files、空檔案、不支援格式或檔名不合法；404：資料夾不存在；409：同名檔案／批次檔名重複；413：請求過大；500：檔案操作失敗。

### 重新命名歌曲

| 項目 | 說明 |
|---|---|
| Purpose | 修改歌曲完整檔名，同步實體檔案。 |
| HTTP Method | `PATCH` |
| End-point | `{{API-Prefix}}/tracks/{id}` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| id | Path | integer | 是 | 歌曲 ID，正整數 |

**Body**

```json
{
  "name": "new-name.mp3"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| name | string | 是 | 新完整檔名；必須保留原音樂格式副檔名 |

**Response — HTTP 200**

```json
{
  "ok": true
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| ok | boolean | 成功時為 true |

**主要錯誤**

400：名稱不合法或變更格式；404：歌曲不存在；409：目的檔名已存在；500：檔案操作失敗。

### 批次搬移歌曲

| 項目 | 說明 |
|---|---|
| Purpose | 批次搬移歌曲，同步資料庫與實體檔案。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/tracks/move` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

```json
{
  "track_ids": [
    1,
    2
  ],
  "folder_id": 3
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| track_ids | integer[] | 是 | 1–1000 個歌曲 ID，重複自動去重 |
| folder_id | integer | 是 | 目的資料夾 ID |

**Response — HTTP 200**

```json
{
  "ok": true,
  "count": 2
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| ok | boolean | 成功時為 true |
| count | integer | 實際操作的歌曲數 |

**行為說明**

已在目的資料夾的歌曲不搬移、不計入 count；搬移後以目的資料夾權限為準。

**主要錯誤**

400：ID 或陣列不合法；404：歌曲或目的資料夾不存在；500：檔案操作失敗。409：目的地有同名檔案或選取歌曲檔名互相衝突，整批不搬移。

### 批次刪除歌曲

| 項目 | 說明 |
|---|---|
| Purpose | 批次刪除歌曲，同步資料庫與實體檔案。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/tracks/delete` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

```json
{
  "track_ids": [
    1,
    2
  ]
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| track_ids | integer[] | 是 | 1–1000 個歌曲 ID，重複自動去重 |

**Response — HTTP 200**

```json
{
  "ok": true,
  "count": 2
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| ok | boolean | 成功時為 true |
| count | integer | 實際操作的歌曲數 |

**行為說明**

永久刪除，沒有復原 API；前端呼叫前應讓使用者確認。

**主要錯誤**

400：ID 或陣列不合法；404：歌曲或目的資料夾不存在；500：檔案操作失敗。

<a id="sync"></a>
## 掃描並同步音樂庫


### 手動同步音樂庫

| 項目 | 說明 |
|---|---|
| Purpose | 掃描實體音樂目錄，將差異同步至資料庫。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/library/sync` |
| 權限 | admin |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
{
  "folders_added": 2,
  "folders_removed": 1,
  "tracks_added": 8,
  "tracks_removed": 3,
  "skipped_count": 1,
  "skipped": [
    {
      "path": "Jazz/notes.txt",
      "reason": "不支援的音樂格式"
    }
  ]
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| folders_added | integer | 新增資料夾數 |
| folders_removed | integer | 移除資料夾數 |
| tracks_added | integer | 新增歌曲數 |
| tracks_removed | integer | 移除歌曲數 |
| skipped_count | integer | 完整略過數 |
| skipped | object[] | 略過明細，最多 100 筆 |
| skipped[].path | string | 相對音樂根目錄的路徑 |
| skipped[].reason | string | 略過原因 |

**行為說明**

同步完成才回應；不修改實體檔案。只掃描根目錄下一層資料夾，新資料夾不自動授權。詳細刪除／保留規則見文末補充。

**主要錯誤**

500：目錄不存在或讀取失敗，掃描未完成不更新 DB。

<a id="playback"></a>
## 播放器與串流

播放器 API 不保存帳號共用的當前歌曲。換歌先 resolve 取得歌曲 ID，再用該 ID 串流；不同視窗不會因另一處按下一首而被切換。

### 取得指定歌曲音訊

| 項目 | 說明 |
|---|---|
| Purpose | 傳送明確指定歌曲的音訊；不負責換歌。 |
| HTTP Method | `GET / HEAD` |
| End-point | `{{API-Prefix}}/tracks/{id}/stream` |
| 權限 | 登入 |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| id | Path | integer | 是 | 歌曲 ID，正整數 |

**Body**

N/A。可選 Header：`Range: bytes=0-1023`；串流可用登入 Cookie 取代 Bearer。

**Response — HTTP 200**

依下方說明回傳。

**行為說明**

Response 是二進位音訊，不是 JSON。完整 GET 為 200；有效範圍 GET 為 206 並帶 Content-Range。HEAD 不含音訊 Body。支援單一 byte Range 與 seek；每次請求重新驗證權限。

**主要錯誤**

403：無歌曲資料夾權限；404：歌曲或檔案不存在；416：Range 無法滿足。

| 副檔名 | Response Content-Type |
|---|---|
| mp3 | audio/mpeg |
| m4a | audio/mp4 |
| wav | audio/wav |
| ogg | audio/ogg |

```http
HTTP/1.1 206 Partial Content
Content-Type: audio/mpeg
Content-Range: bytes 0-1023/123456
Content-Length: 1024
Cache-Control: no-store
```

上方檔案總長為示例；後面接原始音訊 bytes。

### 驗證並整理播放狀態

| 項目 | 說明 |
|---|---|
| Purpose | 驗證並整理播放狀態，不寫入伺服器播放紀錄。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/playback/state` |
| 權限 | 登入 |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

```json
{
  "schema_version": 1,
  "track_id": 123,
  "position": 85.5,
  "mode": "folder"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| schema_version | integer | 否 | 固定 1；省略為 1 |
| track_id | integer / null | 否 | 目前歌曲 ID；省略為 null |
| position | number | 否 | 非負有限秒數；省略為 0，無歌曲時標準化為 0 |
| mode | string | 否 | single / folder / global / random；省略為 folder |

**Response — HTTP 200**

```json
{
  "state": {
    "schema_version": 1,
    "track_id": 123,
    "position": 85.5,
    "mode": "folder"
  },
  "stream_url": "/api/v1/mplayer/tracks/123/stream",
  "reason": null
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| state | object | 標準化完整狀態，欄位與 Body 相同 |
| stream_url | string / null | 同源音訊相對路徑；無歌曲時 null |
| reason | string / null | 一般為 null；restore 清空失效歌曲時為 unavailable |

**行為說明**

省略欄位採預設值，不與舊狀態合併。由呼叫端將回傳 state 存入 localStorage。

**主要錯誤**

400：狀態欄位不合法。403：歌曲無權限；404：歌曲或檔案不存在。

### 恢復本機播放狀態

| 項目 | 說明 |
|---|---|
| Purpose | 恢復本機播放狀態，不寫入伺服器播放紀錄。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/playback/restore` |
| 權限 | 登入 |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

```json
{
  "schema_version": 1,
  "track_id": 123,
  "position": 85.5,
  "mode": "folder"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| schema_version | integer | 否 | 固定 1；省略為 1 |
| track_id | integer / null | 否 | 目前歌曲 ID；省略為 null |
| position | number | 否 | 非負有限秒數；省略為 0，無歌曲時標準化為 0 |
| mode | string | 否 | single / folder / global / random；省略為 folder |

**Response — HTTP 200**

```json
{
  "state": {
    "schema_version": 1,
    "track_id": 123,
    "position": 85.5,
    "mode": "folder"
  },
  "stream_url": "/api/v1/mplayer/tracks/123/stream",
  "reason": null
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| state | object | 標準化完整狀態，欄位與 Body 相同 |
| stream_url | string / null | 同源音訊相對路徑；無歌曲時 null |
| reason | string / null | 一般為 null；restore 清空失效歌曲時為 unavailable |

**行為說明**

省略欄位採預設值，不與舊狀態合併。歌曲遺失或無權限仍回 200，清空歌曲與進度、保留合法 mode；不會自動開始播放。

**主要錯誤**

400：狀態欄位不合法。

歌曲無法恢復時的 HTTP 200 Response：

```json
{
  "state": {
    "schema_version": 1,
    "track_id": null,
    "position": 0,
    "mode": "folder"
  },
  "stream_url": null,
  "reason": "unavailable"
}
```

### 解析上一首／下一首

| 項目 | 說明 |
|---|---|
| Purpose | 依目前歌曲及模式計算目標歌曲 ID。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/playback/resolve` |
| 權限 | 登入 |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

```json
{
  "track_id": 123,
  "mode": "folder",
  "direction": 1,
  "trigger": "manual"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| track_id | integer / null | 否 | 目前歌曲 ID；省略為 null |
| mode | string | 否 | single / folder / global / random；省略為 folder |
| direction | integer | 否 | 1 下一首（預設），-1 上一首 |
| trigger | string | 否 | manual 手動操作（預設），ended 自然播完 |
| schema_version | integer | 否 | 固定 1；省略為 1 |
| position | number | 否 | 非負有限秒數；省略為 0，無歌曲時標準化為 0 |

**Response — HTTP 200**

```json
{
  "track_id": 124,
  "stream_url": "/api/v1/mplayer/tracks/124/stream"
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| track_id | integer | 目標歌曲 ID |
| stream_url | string | 目標歌曲的同源串流相對路徑 |

**行為說明**

schema_version、position 僅作狀態驗證，不影響排序。目標歌曲串流時還會再次驗證檔案與權限。各模式規則見文末補充。

**主要錯誤**

400：參數不合法；403：目前歌曲無權限；404：目前歌曲／檔案不存在，或無可選曲目。

<a id="hls"></a>
## HLS 背景連播（1.3.0）

原始 `/tracks/{id}/stream` 保留。HLS 每次建立獨立工作階段，模式與佇列不依帳號共用；工作階段屬於建立者，其他帳號無法讀取。Server 產生 AAC MPEG-TS 片段與 EVENT 清單，片段請求會補足目前已請求曲目後的兩首，因此自然跨曲不需要 JavaScript 呼叫 resolve。

### 工作階段回應欄位

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | string | 隨機工作階段 ID；不是認證 Token |
| playlist_url | string | 同源 HLS 清單路徑 |
| ready | boolean | 第一首已轉換完成，可以載入清單 |
| entries | object[] | 已準備的歌曲時間對照；不代表使用者已經聽到 |
| entries[].index | integer | 本工作階段內的播放次序，從 0 開始；同曲重播有不同 index |
| entries[].track_id | integer | 歌曲 ID |
| entries[].name | string | 完整檔名 |
| entries[].start | number | HLS 累積媒體時間，秒 |
| entries[].duration | number | 轉碼後片段總長，秒，可能含 AAC 編碼補齊 |
| mode | string | single / folder / global / random |
| position | number | 第一首起播秒數，ready 時限制在其長度內；不是即時播放位置 |
| error | string / null | 背景轉碼錯誤；失敗時請重新建立或切回原始串流 |
| finished | boolean | 已到工作階段長度上限，不再補曲 |

前端以實際 audio.currentTime 對照 entries 計算歌曲與歌曲內秒數。此版清單提供 EXT-X-PROGRAM-DATE-TIME 與 EXT-X-DATERANGE 的 X-TRACK-ID，未注入 Timed ID3，亦不保證 Safari 將自訂 tag 自動轉成事件。不能將最後下載的片段視為最後聽到的位置。


### 檢查 HLS 可用性

| 項目 | 說明 |
|---|---|
| Purpose | 檢查設定與 FFmpeg 執行檔。 |
| HTTP Method | `GET` |
| End-point | `{{API-Prefix}}/hls/status` |
| 權限 | 登入；工作階段只限建立者 |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

N/A

**Body**

N/A（不需 Body）。



**Response — HTTP 200**

```json
{
  "enabled": true,
  "reason": null
}
```

**行為說明**

enabled 為 boolean；reason 為停用原因字串或 null。不檢查每首歌曲是否可解碼。

**主要錯誤**

401：未登入。


### 建立 HLS 工作階段

| 項目 | 說明 |
|---|---|
| Purpose | 建立獨立播放佇列並排入背景轉碼。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/hls/sessions` |
| 權限 | 登入；工作階段只限建立者 |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json` |

**URL Param**

N/A

**Body**

```json
{
  "track_id": 123,
  "position": 85.5,
  "mode": "folder"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| track_id | integer | 是 | 可播放歌曲 ID，不可為 null |
| position | number | 否 | 非負有限秒數，預設 0 |
| mode | string | 否 | single / folder / global / random，預設 folder |
| schema_version | integer | 否 | 固定 1，預設 1 |

**Response — HTTP 202**

```json
{
  "id": "<session_id>",
  "playlist_url": "/api/v1/mplayer/tracks/hls/<session_id>/index.m3u8",
  "ready": false,
  "entries": [],
  "mode": "folder",
  "position": 85.5,
  "error": null,
  "finished": false
}
```

**行為說明**

回傳 202 不表示已可播放；輪詢 GET 工作階段，直到 ready=true。若有 error 應停止等待。第一首完整轉換後才提供片段；已有快取則可快速使用。

**主要錯誤**

400：狀態不合法；403：沒有歌曲權限；404：歌曲／檔案不存在；503：停用、缺少 FFmpeg 或工作階段達上限。


### 查詢 HLS 工作階段

| 項目 | 說明 |
|---|---|
| Purpose | 取得準備進度與歌曲時間表。 |
| HTTP Method | `GET` |
| End-point | `{{API-Prefix}}/hls/sessions/{sid}` |
| 權限 | 登入；工作階段只限建立者 |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| sid | Path | string | 是 | 建立時回傳的 id |

**Body**

N/A（不需 Body）。



**Response — HTTP 200**

```json
{
  "id": "<session_id>",
  "playlist_url": "/api/v1/mplayer/tracks/hls/<session_id>/index.m3u8",
  "ready": true,
  "entries": [
    {
      "index": 0,
      "track_id": 123,
      "name": "song.mp3",
      "start": 0,
      "duration": 240.021333
    }
  ],
  "mode": "folder",
  "position": 85.5,
  "error": null,
  "finished": false
}
```

**行為說明**

只回傳已準備的 entries，不是即時聆聽狀態。頁面回到前景時重新取得時間表，再對照播放器位置。

**主要錯誤**

401：未登入；403／404：佇列歌曲權限被移除或歌曲刪除；404：工作階段不存在、過期或不屬於目前帳號。


### 停止 HLS 工作階段

| 項目 | 說明 |
|---|---|
| Purpose | 移除工作階段，禁止後續片段讀取。 |
| HTTP Method | `DELETE` |
| End-point | `{{API-Prefix}}/hls/sessions/{sid}` |
| 權限 | 登入；工作階段只限建立者 |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| sid | Path | string | 是 | 建立時回傳的 id |

**Body**

N/A（不需 Body）。



**Response — HTTP 200**

```json
{
  "ok": true
}
```

**行為說明**

已下載資料無法追回；共享歌曲快取不立即刪除。正在進行的單首轉碼可能繼續完成，但不再添加至已刪工作階段。

**主要錯誤**

401：未登入；404：工作階段不存在、過期或不屬於目前帳號。


### 取得 HLS 播放清單

| 項目 | 說明 |
|---|---|
| Purpose | 取得持續擴展的 EVENT 播放清單。 |
| HTTP Method | `GET / HEAD` |
| End-point | `{{API-Prefix}}/tracks/hls/{sid}/index.m3u8` |
| 權限 | 登入；工作階段只限建立者 |
| HTTP Header | `Authorization: Bearer <access_token>`；也可用 mplayer_media Cookie |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| sid | Path | string | 是 | 建立時回傳的 id |

**Body**

N/A（不需 Body）。



**Response — HTTP 200**

回傳媒體內容，見下方說明。

**行為說明**

Content-Type: application/vnd.apple.mpegurl；Cache-Control: no-store。EXT-X-START 的 TIME-OFFSET 為工作階段第一首的實際起播秒數（與 position 一致）。前端等目標可 seek 並確認定位後才播放；不在 loadedmetadata 階段直接 seek。Body 是 m3u8 文字，包含約 6 秒片段、歌曲邊界 DISCONTINUITY、時間與曲目 ID。HEAD 無 Body。每次請求檢查目前帳號與佇列歌曲權限。清單達 1000 首或累積約 24 小時後加 ENDLIST；轉碼失敗也結束既有清單。

**主要錯誤**

401：認證失效；403：歌曲權限撤銷；404：工作階段／歌曲不存在；503：第一首仍在準備或準備失敗。


### 取得 HLS 音訊片段

| 項目 | 說明 |
|---|---|
| Purpose | 取得清單中指定的音訊片段。 |
| HTTP Method | `GET / HEAD` |
| End-point | `{{API-Prefix}}/tracks/hls/{sid}/{entry_index}/{segment_index}.ts` |
| 權限 | 登入；工作階段只限建立者 |
| HTTP Header | `Authorization: Bearer <access_token>`；也可用 mplayer_media Cookie |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| sid | Path | string | 是 | 建立時回傳的 id |
| entry_index | Path | integer | 是 | 歌曲播放次序，從 0 開始 |
| segment_index | Path | integer | 是 | 該次歌曲的片段序號，從 0 開始 |

**Body**

N/A（不需 Body）。



**Response — HTTP 200**

回傳媒體內容，見下方說明。

**行為說明**

使用清單內的 URL，不自行推算。Content-Type: video/mp2t（MPEG-TS 容器，只有 AAC 音訊）；200 完整片段，合法 byte Range 可回 206。檢查所屬曲目的資料夾權限；每次請求使工作階段續期並觸發預先補曲。

**主要錯誤**

401：認證失效；403：歌曲權限撤銷；404：工作階段、歌曲或片段不存在；416：Range 無法滿足。


### 保存、清理與限制

- 播放方式以 localStorage 的 `mplayer:transport:v1` 保存 `direct` 或 `hls`；與既有循環模式分開，不會因其他視窗變動而自動切換。
- 歌曲／進度仍以 `mplayer:playback:v1:<user_id>` 保存。下次先用 `/playback/restore` 驗證，使用者按播放才建立新 HLS 工作階段；不保存舊 HLS URL 作為永久續播網址。
- 單曲循環自然播完會重播同曲；手動上下首仍走原有 resolve 規則。其他模式的手動上下首優先使用已準備的 entries；隨機不保存跨工作階段歷史。
- 狀態／清單／片段請求會更新閒置時間；預設 3600 秒回收，最多 32 個工作階段。程序重啟即失效；只支援單一 Gunicorn worker。
- 快取重用依原檔路徑、大小與修改時間識別。cache_mb 預設 2048，僅清理未被工作階段使用的舊快取，不是硬性磁碟上限。
- 音訊 Cookie 有效期仍為登入後 24 小時；HLS 不延長登入。iOS 背景 JS 可能暫停，所以曲名更新與本機最後保存位置可能延遲。


<a id="passkeys"></a>
## Face ID／通行密鑰

使用 WebAuthn；Face ID、Touch ID、Windows Hello 或 PIN 由作業系統決定，網站不接收生物辨識資料。需先以密碼登入並綁定。所有 options／verify POST 需要符合設定的 Origin；瀏覽器會自動帶入。

註冊流程：register/options → navigator.credentials.create → register/verify。登入流程：login/options → navigator.credentials.get → login/verify。以下 base64url 及 challenge 範例為佔位符，不可直接拿來驗證。

### 取得通行密鑰狀態

| 項目 | 說明 |
|---|---|
| Purpose | 確認後端是否已啟用且設定有效。 |
| HTTP Method | `GET` |
| End-point | `{{API-Prefix}}/passkeys/status` |
| 權限 | 公開 |
| HTTP Header | 無必要的應用程式標頭 |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
{
  "enabled": true,
  "origin": "https://music.example.com:18443"
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| enabled | boolean | 後端是否完成啟用與設定檢查 |
| origin | string / null | 允許的精確 origin；停用為 null |

**行為說明**

不代表此瀏覽器必然支援 WebAuthn。rp_id 只含主機名，不含 port；origin 包含協定與非預設 port，不加尾端斜線。

### 列出自己的通行密鑰

| 項目 | 說明 |
|---|---|
| Purpose | 列出目前帳號綁定的密鑰，依 ID 排序。 |
| HTTP Method | `GET` |
| End-point | `{{API-Prefix}}/passkeys` |
| 權限 | 登入 |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
[
  {
    "id": 1,
    "name": "我的 iPhone",
    "created": 1790000000.5,
    "last_used": null
  }
]
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| [].id | integer | 密鑰記錄 ID |
| [].name | string | 使用者命名 |
| [].created | number | 建立時間，Unix 秒 |
| [].last_used | number / null | 最近登入使用時間，Unix 秒；未使用為 null |

**行為說明**

不回傳其他帳號密鑰、私鑰、公鑰或原始 credential ID。

### 移除自己的通行密鑰

| 項目 | 說明 |
|---|---|
| Purpose | 解除目前帳號的一組密鑰。 |
| HTTP Method | `DELETE` |
| End-point | `{{API-Prefix}}/passkeys/{id}` |
| 權限 | 登入 |
| HTTP Header | `Authorization: Bearer <access_token>` |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| id | Path | integer | 是 | 通行密鑰記錄 ID，正整數 |

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
{
  "ok": true
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| ok | boolean | 成功時為 true |

**行為說明**

不撤銷已登入 session；不會移除裝置密鑰管理器中的本機項目。

**主要錯誤**

404：密鑰不存在或不屬於目前帳號。

### 取得綁定驗證選項

| 項目 | 說明 |
|---|---|
| Purpose | 產生一次性的 WebAuthn challenge。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/passkeys/register/options` |
| 權限 | 登入 |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json`<br>`Origin: {{Site-Origin}}`（瀏覽器自動帶入，必須與設定完全相同） |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

```json
{
  "name": "我的 iPhone",
  "password": "admin"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| name | string | 否 | 1–80 字元，去除前後空白；預設「我的通行密鑰」 |
| password | string | 是 | 目前帳號密碼 |

**Response — HTTP 200**

```json
{
  "challenge_id": "<challenge_id>",
  "options": {
    "rp": {
      "id": "music.example.com",
      "name": "SONORA"
    },
    "user": {
      "id": "MQ",
      "name": "super",
      "displayName": "super"
    },
    "challenge": "<base64url-challenge>",
    "pubKeyCredParams": [
      {
        "type": "public-key",
        "alg": -7
      },
      {
        "type": "public-key",
        "alg": -257
      }
    ],
    "timeout": 120000,
    "excludeCredentials": [],
    "authenticatorSelection": {
      "residentKey": "required",
      "requireResidentKey": true,
      "userVerification": "required"
    },
    "attestation": "none"
  }
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| challenge_id | string | verify 必須帶回的請求識別碼 |
| options | object | 傳給瀏覽器的 WebAuthn 選項；須先還原二進位欄位 |
| options.challenge | string | base64url challenge |
| options.timeout | integer | 瀏覽器操作逾時毫秒，120000 |
| options.rp | object | 站台名稱與 RP ID |
| options.user | object | 帳號資料，id 為 base64url |
| options.pubKeyCredParams | object[] | 伺服器允許的演算法，使用實際回應清單 |
| options.excludeCredentials | object[] | 已註冊憑證，避免重複 |
| options.authenticatorSelection | object | 要求 resident key 與使用者驗證 |
| options.attestation | string | 註冊證明偏好 |

**行為說明**

options 示意僅供理解；實際選項可能包含額外欄位，請完整使用後端回應。此 challenge 綁定目前帳號與 Bearer session。

**主要錯誤**

400：資料不合法。403：目前密碼錯誤；409：已達 20 組密鑰。403：Origin 不符；503：未設定。

### 完成通行密鑰綁定

| 項目 | 說明 |
|---|---|
| Purpose | 驗證瀏覽器回傳的 WebAuthn 憑證與 challenge。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/passkeys/register/verify` |
| 權限 | 登入 |
| HTTP Header | `Authorization: Bearer <access_token>`<br>`Content-Type: application/json`<br>`Origin: {{Site-Origin}}`（瀏覽器自動帶入，必須與設定完全相同） |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

```json
{
  "challenge_id": "<challenge_id>",
  "credential": {
    "id": "<base64url-credential-id>",
    "rawId": "<base64url-credential-id>",
    "type": "public-key",
    "response": {
      "clientDataJSON": "<base64url-client-data>",
      "attestationObject": "<base64url-attestation>",
      "transports": [
        "internal"
      ]
    },
    "clientExtensionResults": {}
  }
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| challenge_id | string | 是 | 同流程 options 回傳的識別碼 |
| credential | object | 是 | 瀏覽器 PublicKeyCredential 序列化結果 |
| credential.id / rawId | string | 是 | 同一 credential ID 的 base64url 表示 |
| credential.type | string | 是 | 固定 public-key |
| credential.response.clientDataJSON | string | 是 | base64url 原始 clientDataJSON |
| credential.response.attestationObject | string | 是 | 瀏覽器回傳二進位資料的 base64url |
| credential.response.transports | string[] | 否 | getTransports() 回傳傳輸方式 |
| credential.clientExtensionResults | object | 否 | getClientExtensionResults() 回傳值 |

**Response — HTTP 201**

```json
{
  "ok": true
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| ok | boolean | 成功時為 true |

**行為說明**

challenge 有效 120 秒，只可使用一次；同帳號同流程重新取得 options 會使舊 challenge 失效。驗證失敗後應重新取得 options。綁定成功不建立新登入 session。

**主要錯誤**

400：challenge 過期／已使用、簽章或 WebAuthn 驗證失敗；403：Origin 不符或 challenge 不屬於此登入；409：憑證重複。503：未設定。

### 取得登入驗證選項

| 項目 | 說明 |
|---|---|
| Purpose | 產生一次性的 WebAuthn challenge。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/passkeys/login/options` |
| 權限 | 公開 |
| HTTP Header | `Content-Type: application/json`<br>`Origin: {{Site-Origin}}`（瀏覽器自動帶入，必須與設定完全相同） |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

```json
{
  "username": "super"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| username | string | 是 | 已綁定通行密鑰的帳號名稱 |

**Response — HTTP 200**

```json
{
  "challenge_id": "<challenge_id>",
  "options": {
    "challenge": "<base64url-challenge>",
    "timeout": 120000,
    "userVerification": "required",
    "rpId": "music.example.com",
    "allowCredentials": [
      {
        "id": "<base64url-credential-id>",
        "type": "public-key"
      }
    ]
  }
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| challenge_id | string | verify 必須帶回的請求識別碼 |
| options | object | 傳給瀏覽器的 WebAuthn 選項；須先還原二進位欄位 |
| options.challenge | string | base64url challenge |
| options.timeout | integer | 瀏覽器操作逾時毫秒，120000 |
| options.rpId | string | 驗證站台 RP ID |
| options.allowCredentials | object[] | 該帳號可使用的憑證，id 為 base64url |
| options.userVerification | string | required，要求裝置使用者驗證 |

**行為說明**

options 示意僅供理解；實際選項可能包含額外欄位，請完整使用後端回應。此步驟尚未登入，成功驗證簽章後才會建立 session。

**主要錯誤**

400：資料不合法或帳號不存在／未綁定密鑰。403：Origin 不符；503：未設定。

### 完成通行密鑰登入

| 項目 | 說明 |
|---|---|
| Purpose | 驗證瀏覽器回傳的 WebAuthn 憑證與 challenge。 |
| HTTP Method | `POST` |
| End-point | `{{API-Prefix}}/passkeys/login/verify` |
| 權限 | 公開 |
| HTTP Header | `Content-Type: application/json`<br>`Origin: {{Site-Origin}}`（瀏覽器自動帶入，必須與設定完全相同） |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

```json
{
  "challenge_id": "<challenge_id>",
  "credential": {
    "id": "<base64url-credential-id>",
    "rawId": "<base64url-credential-id>",
    "type": "public-key",
    "response": {
      "clientDataJSON": "<base64url-client-data>",
      "authenticatorData": "<base64url-authenticator-data>",
      "signature": "<base64url-signature>",
      "userHandle": "MQ"
    },
    "clientExtensionResults": {}
  }
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| challenge_id | string | 是 | 同流程 options 回傳的識別碼 |
| credential | object | 是 | 瀏覽器 PublicKeyCredential 序列化結果 |
| credential.id / rawId | string | 是 | 同一 credential ID 的 base64url 表示 |
| credential.type | string | 是 | 固定 public-key |
| credential.response.clientDataJSON | string | 是 | base64url 原始 clientDataJSON |
| credential.response.authenticatorData | string | 是 | 瀏覽器回傳二進位資料的 base64url |
| credential.response.signature | string | 是 | base64url 簽章 |
| credential.response.userHandle | string / null | 否 | 若非 null，必須對應此帳號 |
| credential.clientExtensionResults | object | 否 | getClientExtensionResults() 回傳值 |

**Response — HTTP 200**

```json
{
  "access_token": "<access_token>",
  "token_type": "Bearer",
  "expires_in": 86400,
  "user": {
    "id": 1,
    "username": "super",
    "role": "admin"
  }
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| access_token | string | 後續 API 使用的 Token |
| token_type | string | 固定 Bearer |
| expires_in | integer | 有效秒數，固定 86400 |
| user.id | integer | 登入帳號 ID |
| user.username | string | 帳號名稱 |
| user.role | string | admin 或 user |

**行為說明**

challenge 有效 120 秒，只可使用一次；同帳號同流程重新取得 options 會使舊 challenge 失效。驗證失敗後應重新取得 options。成功同時設定音訊 Cookie，回應與密碼登入相同。

**主要錯誤**

400：challenge 過期／已使用、簽章或 WebAuthn 驗證失敗；403：Origin 不符。503：未設定。

### WebAuthn 編碼與大小限制

options 的 challenge、user.id、allowCredentials[].id、excludeCredentials[].id 必須從 base64url 轉為 ArrayBuffer／Uint8Array，再傳給 navigator.credentials.create/get。verify 將瀏覽器回應中的二進位欄位轉回不含 padding 的 base64url。可參考 frontend/passkeys.js 的轉換實作。

四支 options／verify 端點請求上限為 64 KiB，超過回傳 413。伺服器驗證 challenge、RP ID、origin、使用者驗證、簽章與簽章計數；僅傳「驗證成功」布林值無效。

<a id="health"></a>
## 健康檢查與靜態資源


### 健康檢查

| 項目 | 說明 |
|---|---|
| Purpose | 查詢 SQLite 以檢查服務健康。 |
| HTTP Method | `GET` |
| End-point | `{{Site-Origin}}/health` |
| 權限 | 公開 |
| HTTP Header | 無必要的應用程式標頭 |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

```json
{
  "status": "ok"
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| status | string | 正常為 ok |

**行為說明**

此路徑沒有 /api/v1/mplayer 前綴。

### 前端入口

| 項目 | 說明 |
|---|---|
| Purpose | 取得登入頁／音樂播放器。 |
| HTTP Method | `GET` |
| End-point | `{{Site-Origin}}/` |
| 權限 | 公開 |
| HTTP Header | 無必要的應用程式標頭 |

**URL Param**

N/A（無路徑／查詢參數）。

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

依下方說明回傳。

**行為說明**

回傳 text/html，Body 為 frontend/index.html。

### 前端靜態資源

| 項目 | 說明 |
|---|---|
| Purpose | 取得前端 CSS、JavaScript、圖示等資源。 |
| HTTP Method | `GET` |
| End-point | `{{Site-Origin}}/assets/{filename}` |
| 權限 | 公開 |
| HTTP Header | 無必要的應用程式標頭 |

**URL Param**

| 欄位 | 位置 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| filename | Path | string | 是 | 相對 frontend 的檔案路徑，可含子目錄，例如 icons/apple-touch-icon.png |

**Body**

N/A（不需 Body）。

**Response — HTTP 200**

依下方說明回傳。

**行為說明**

回傳實際檔案內容，Content-Type 依檔案類型。不是音樂檔案存取端點。

**主要錯誤**

404：資源不存在。

<a id="example"></a>
## 完整呼叫範例

```bash
API='http://localhost:8000/api/v1/mplayer'

# 1. 登入，從回應複製 access_token
curl -X POST "$API/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"super","password":"admin"}'
TOKEN='貼上實際 access_token'

# 2. 列出可見資料夾與歌曲
curl "$API/folders" -H "Authorization: Bearer $TOKEN"
curl "$API/tracks?folder_id=1" -H "Authorization: Bearer $TOKEN"

# 3. 取得下一首（使用實際歌曲 ID）
curl -X POST "$API/playback/resolve" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"track_id":1,"mode":"folder","direction":1,"trigger":"manual"}'

# 4. 使用回應的 stream_url，例如歌曲 2
curl "$API/tracks/2/stream" \
  -H "Authorization: Bearer $TOKEN" -H 'Range: bytes=0-1023' \
  --output first-1024-bytes.bin
```

<a id="notes"></a>
## 同步、播放器與遷移補充

### 同步細節

- 以 `storage.music` 當前檔案清單更新 DB，只掃描一層實體資料夾內 mp3/m4a/wav/ogg（副檔名不分大小寫）。空資料夾也登錄。
- 新資料夾不產生任何 user 授權；同路徑既有資料夾保留 ID 與授權，同路徑曲目保留 ID，因此本機續播紀錄仍可使用。
- DB 中不存在於有效掃描清單的歌曲、資料夾會移除。刪除資料夾紀錄也會清除其授權；本機紀錄指向已刪曲目時，恢復 API 將 track_id 設為 null、position 歸零。
- 外部改名／搬移視為舊路徑刪除、新路徑新增，不推測檔案身份，不繼承舊授權或進度。
- 略過根目錄檔案、巢狀資料夾（不遞迴）、符號連結、非一般檔案、空檔案、不支援格式及不符合名稱規則的項目；`skipped` 最多列出 100 筆，`skipped_count` 為完整略過數。
- 完整掃描成功後才在同一 SQLite 交易中寫入；目錄不存在或讀取失敗回傳 500，DB 保持原狀。掃描不寫入、搬移或刪除實際檔案。
- 可重複執行，未變動時新增／移除數均為 0。請完成外部檔案操作後再掃描，外部程序不受 API 操作鎖保護。
- 同步為手動觸發；大量檔案需等待，部署時應依規模調整 HTTP timeout。掛載磁碟若呈現空目錄會視為已清空，請先確認儲存可用。
### 選曲模式

- direction 為 1（預設，下一首）或 -1（上一首）；trigger 為 manual（預設）或 ended。
- mode 預設 folder；folder 在目前歌曲資料夾循環，global 在所有授權歌曲循環。
- random 從所有授權歌曲隨機選曲，有其他歌曲時排除目前歌曲；只有一首時重播。API 的 direction=1/-1 都回傳隨機候選，不保存歷史；網頁播放器自行保留本視窗最近 200 筆隨機歷史，上一首優先回到歷史歌曲，下一首優先使用前進歷史。重載頁面、選歌或改模式會清除歷史，無歷史時使用 API 隨機候選。
- single + ended 重播原曲；single + manual 在目前資料夾切換上一首／下一首。
- 排序依資料夾名稱、檔名、ID；首尾循環。非 random 模式下，track_id=null 或省略時，下一首選全部授權歌曲第一首，上一首選最後一首；random 仍隨機選取。
- 目前歌曲不存在／檔案遺失回傳 404，無權限回傳 403；無可選歌曲為 404。目標歌曲在串流時仍會重新檢查權限與檔案。
- resolve 僅計算目標，不更改任何播放器或帳號狀態；GET 串流也絕不換歌。

瀏覽器保存方式：

- localStorage key 為 `mplayer:playback:v1:<user_id>`，只保存完整狀態 JSON，不保存 Token。
- 每 5 秒、暫停、跳轉、換歌、模式變更、登出及離頁時在本機保存；週期儲存不呼叫後端。
- 同瀏覽器設定檔、同 origin、同帳號視窗共用續播紀錄，最後寫入為準；不將其他視窗的 storage 變更套用到正在播放的音訊。不同裝置／瀏覽器各自記憶。
- 儲存遭封鎖／容量不足時提示，仍可在本視窗播放；清除網站資料會失去續播紀錄。損壞或不支援版本的紀錄重設為預設狀態。
### 舊版遷移

舊 GET/PUT `/playback`、POST `/playback/next`、GET `/playback/stream` 已移除（帶有效認證回傳 404）。舊 DB playback 表保留但不再用於播放，也不自動匯入本機紀錄。升級後第一次登入從空狀態開始，既有登入請重新登入以取得新 Cookie 路徑。新增 id_counters 表記錄帳號／歌曲 ID 高水位，保留既有資料表與 ID，之後由 API 新增的資料不重用已刪除 ID，避免本機舊紀錄指向新資料。
### 帳號與通行密鑰資料

新增 passkeys 與 passkey_challenges 資料表，不刪除現有帳號或資料。修改帳號會清除未完成 challenge 與登入 session，但保留已綁定通行密鑰；不再需要的密鑰請在設定中移除。刪除帳號則連同密鑰及 challenge 一併刪除。公鑰與憑證 ID 保存在 DB，私鑰由裝置／密鑰管理器保管。
