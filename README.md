# SONORA · Music Player Server

Python Flask WebAPI + SQLite + 獨立 HTML/CSS/JavaScript 前端。頁面會自動跟隨系統的淺色／深色模式，切換系統外觀時即時更新。前端只透過 API 取得資料，伺服器同源提供靜態資源，不需 Node.js 建置。

## 快速啟動

Python 3.12+，在 Linux / WSL 專案根目錄執行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

開啟 http://localhost:8000 。預設 admin 帳號：`super` / `admin`，首次登入後請到帳號管理修改密碼。只在空帳號資料庫初始化此帳號，不會於重啟重設密碼。

正式執行使用 `gunicorn -c gunicorn.conf.py run:app`，或參考 [Docker ReadMe](docker/ReadMe.md)。Windows 原生可使用 `python run.py` 進行開發；Gunicorn 用於 Linux / Docker。

## 文件導航

- [更新紀錄](CHANGELOG.md)
- [完整 WebAPI 文件](docs/API.md)
- [Docker 建置、部署與備份](docker/ReadMe.md)
- [YAML 設定](config.yaml)

## 使用流程

1. admin 登入，在左側新增音樂資料夾。
2. 選擇資料夾後上傳 mp3、m4a、wav、ogg，可一次多選。上傳限制預設為每次請求合計 200 MiB（包含 multipart 開銷）。
3. 在帳號管理新增 user，再開啟「存取權限」，先選擇 user，勾選可存取的多個音樂資料夾並一次儲存。也可從帳號列表的 user 列直接開啟權限設定。
4. user 登入僅能看到授權資料夾，點選任一歌曲即可播放。
5. 底部播放器跨畫面保留。可切換單曲、資料夾、全局循環與隨機播放；記憶曲目、秒數與循環模式，重新登入後按播放續聽。
6. admin 可修改檔名、批量搬移或批量刪除。刪除前會顯示不可復原警告。資料夾需清空才能刪除。

## 架構與資料

```text
backend/app.py       API、認證、SQLite、檔案管理
frontend/            登入、管理介面、播放器
config.yaml          服務、TLS、儲存、上傳設定
run.py               開發啟動入口
gunicorn.conf.py     正式服務入口設定
docker/              Dockerfile、Compose、部署文件
docs/API.md          API 目錄與規格
tests/               API 整合測試
data/music/資料夾/檔名   實際音樂（執行後建立）
data/music.sqlite3     帳號、授權、登入、音樂索引
```

資料庫與音樂路徑相對於專案根目錄，亦可使用絕對路徑。音樂只支援一層資料夾，檔名與介面保持一致。直接改動儲存目錄後，請由 admin 點「掃描並同步音樂庫」手動更新清單；不會自動監聽檔案變動。掃描會登錄一層實體資料夾中的支援格式，移除已不存在或不符合匯入規則的 DB 紀錄，不會改動實際檔案。新資料夾（包含空資料夾）預設不授權任何 user；原路徑資料夾的授權保留。外部改名或搬移視為移除舊路徑、新增新路徑，不會自動繼承舊資料夾授權或舊曲目播放進度。

請先完成外部複製再同步。根目錄音樂、巢狀資料夾、符號連結、空檔案、未支援格式與不合法名稱會略過。目錄讀取失敗會中止，不更新 DB。若外部磁碟仍掛載為空目錄，系統無法辨別其與真正清空的差別，請先確認掛載正常。

Bearer Token 有效 24 小時，伺服器只保存 Token 的 SHA-256；密碼使用 Werkzeug scrypt 雜湊。一般 API 必須使用 Authorization header，只有音訊 GET 額外接受 HttpOnly / SameSite=Strict Cookie，Token 不會出現在 URL。前端 Bearer Token 放在 sessionStorage，關閉該分頁後需重新登入。

串流使用 `/api/v1/mplayer/tracks/{歌曲ID}/stream`，每次 Range 都明確指定歌曲。上一首／下一首呼叫無狀態的 resolve API，由前端提交目前歌曲、循環模式與方向。單曲循環在播放結束時重播；手動上一首／下一首仍在目前資料夾切歌。支援單一 byte Range、續傳與 seek，原始串流的音訊解碼能力由瀏覽器決定，不做轉碼；HLS 模式會轉成 AAC。

播放狀態存在瀏覽器 localStorage，依帳號區分；每 5 秒、暫停、跳轉、換歌、模式變更與離頁時保存。恢復時交由後端驗證 JSON，再由前端設定進度，不自動播放。不同裝置各自記憶；同瀏覽器多視窗各自播放，續播紀錄以最後寫入為準，不會因另一視窗儲存而換歌。清除網站資料會失去續播紀錄；本機儲存無法使用時會提示，但不阻止播放。權限撤銷會阻止後續串流與 API，已下載音訊無法追回。

升級後請重新登入以更新音訊 Cookie。舊版共用播放 API 已移除；舊 DB 播放紀錄保留但不再使用，新版首次登入從空狀態開始。詳細契約見 API 文件。

同名操作整批拒絕；一般檔案/資料庫失敗會回復已搬動的檔案。SQLite 與檔案系統不是同一筆原子交易，斷電/強制終止時仍需由一致備份復原；刪除時暫存的 `.trash-*` 檔案不會提供播放。請勿以多個程序共用儲存目錄。

## 測試

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## 版本

更新內容請見 [CHANGELOG.md](CHANGELOG.md)。根目錄 `VERSION` 統一管理版號，登入頁與登入後的頁首會顯示版本。修改後需重啟服務；Docker 部署請執行 `bash docker/compose.sh up -d --build`，image tag 與 OCI 版本標籤會自動採用同一版號。

前端播放狀態測試（需 Node.js，只用於測試，站台運行不需要）：`node --test tests/*.cjs`。涵蓋多視窗最後寫入、各自串流、依帳號恢復、進度限制及本機儲存失敗。

## HLS 背景連播

播放器右側可選「原始串流」或「HLS 背景連播」。選擇以 `mplayer:transport:v1` 記在此瀏覽器的 localStorage，下次開啟仍選用同一方式。這個選項與單曲／資料夾／全局／隨機循環分開；不同視窗不會同步切換。恢復歌曲後仍需手動按播放。

HLS 將多首歌曲組成同一條 EVENT 播放清單，片段請求會讓 Server 提前準備後續歌曲，背景自然換歌不需要前端呼叫 resolve。每次播放建立獨立工作階段，同帳號不同視窗互不影響。Safari 優先原生 HLS，其他支援 MSE 的瀏覽器使用本機提供的 hls.js 1.6.13（授權見 frontend/vendor/hls-LICENSE.txt）。

本機 Python 開發需先安裝 FFmpeg，例如 WSL：`sudo apt install ffmpeg`。Docker image 會建置本專案專用的精簡 FFmpeg，沒有使用 ffmpeg-python。未安裝時原始串流仍可使用，HLS 顯示錯誤，不會偷偷改掉使用者選擇。

- 統一轉成 AAC 192 kbps、48 kHz、雙聲道，約 6 秒一段；單一背景轉碼工作，首次播放須等待整首轉換，後續重用快取。原音樂檔不修改。
- HLS 帶有 PROGRAM-DATE-TIME／DATERANGE 曲目 ID；第一版由工作階段 API 的時間表對照播放器時間，未加入 Timed ID3。曲目內進度會寫入既有 localStorage 狀態。
- 下一首／上一首優先跳到此工作階段已準備的曲目；無歷史時使用 resolve。切換循環模式、選歌或切換播放方式會重建串流。HLS 隨機歷史限本工作階段。
- 跳轉以目前歌曲秒數為單位。回到前景會校正曲目、進度與顯示資料夾。背景 JavaScript 被暫停時，本機紀錄可能較舊；關閉後不保證精確接續最後一秒。
- 工作階段只存在單一服務程序記憶體，重啟後失效；預設最多 32 個、閒置 3600 秒回收（收到 playlist、segment 或狀態請求會續期）。每個工作階段最多累積 1000 首或約 24 小時，超過後清單結束；前端在可執行時可重建，不能保證無限背景播放。
- 快取預設在 data/hls-cache，目標上限 2048 MiB；超出時清除未被工作階段引用的舊快取。使用中的快取不刪除，故此值不是磁碟硬上限。轉碼逾時 300 秒會報錯。
- HLS 僅音訊，跨曲不承諾無縫播放；iOS 鎖屏、來電、切換網路與主畫面模式仍需實機驗證。

詳細設定與部署參考 [Docker 文件](docker/ReadMe.md)，端點與狀態欄位見 [API 文件](docs/API.md#hls)。


## 進階設定與快捷 HLS 測試

左側「我的音樂庫」下方的「進階設定」包含 Face ID／通行密鑰設定與免登入快捷 HLS 播放測試。
每個帳號預設關閉測試；勾選啟用後，按「免登入快捷 HLS 播放測試」即可複製 .m3u8 網址，直接交給支援 HLS 的播放器。
設定與專屬連結保存於 SQLite，重啟後保留。關閉即撤銷舊連結，重新啟用會換新網址；已緩衝音訊可能仍短暫播放。
連結持有人只能播放共用測試音效，不能存取音樂庫或管理 API。請勿公開分享連結；代理存取日誌也可能包含網址。

首次啟用使用 FFmpeg 產生六秒 AAC 按鍵音，保存在資料庫旁的 quick-test-audio-v1，所有帳號共用。
此功能獨立於音樂庫 HLS 開關與工作階段，不消耗既有 HLS session 配額。播放清單依時間滑動並重複共享片段，不依賴前端循環。
剪貼簿複製通常需要 HTTPS；失敗時頁面提供手動複製欄位。
不再提供 /quick-test/{token}/ 播放頁面，該路徑回傳 404。CarPlay 捷徑可使用 .m3u8 網址；是否能自動播放由播放器與 iOS 決定，需實機確認。
