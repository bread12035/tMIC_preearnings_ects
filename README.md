# Earnings Intelligence Service

GCP GKE 上的 event-driven 財報情報服務,包含兩個工作流:

- **pre-earnings**:財報記者會前 30 分鐘,週期性監控公司官網,抓取 press release 後產生 summary
- **ects** (earnings call summary):財報記者會後,從 GCS 拉取 Bloomberg transcript / financial / segment 資料,產生 AI summary

兩個 workflow 共用 Pub/Sub、GCS、Claude API,部屬時為兩個獨立的 GKE Deployment。

> 完整設計細節請參考 [`SDD.md`](./SDD.md)。本 README 只涵蓋本機開發環境的建置。

---

## 系統需求

| 項目 | 版本 |
|------|------|
| Python | **3.11.11** |
| OS(本機開發) | Windows 10/11、macOS、Linux 皆可 |
| OS(部屬目標) | Debian Bookworm(Docker `python:3.11.11-bookworm`) |
| GCP SDK(可選) | `gcloud` CLI,本機要連 GCP 資源時需要 |

> 本專案不使用 Poetry / Pipenv / uv。所有 Python 依賴透過原生 `venv` + `pip` + `requirements.txt` 管理。

---

## 快速開始(Windows)

以下指令皆在 **PowerShell** 或 **Command Prompt (cmd)** 中執行。請在 repo 根目錄(`requirements.txt` 所在處)操作。

### 1. 安裝 Python 3.11.11

從 [python.org](https://www.python.org/downloads/release/python-31111/) 下載 Windows installer。安裝時務必勾選:

- ✅ **Add python.exe to PATH**
- ✅ **Install pip**

驗證:

```powershell
python --version
# Python 3.11.11
```

如果你電腦上已經有其他版本的 Python,改用 [`py launcher`](https://docs.python.org/3/using/windows.html#python-launcher-for-windows) 指定版本:

```powershell
py -3.11 --version
# Python 3.11.11
```

下面的指令凡是 `python`,在多版本機器上都改用 `py -3.11` 替代。

### 2. 建立 venv(虛擬環境)

```powershell
# 在 repo 根目錄執行
python -m venv .venv
```

這會建立 `.venv\` 資料夾,裡面包含獨立的 Python interpreter 和 site-packages。**請將 `.venv/` 加入 `.gitignore`,絕對不要 commit。**

### 3. 啟用 venv

**PowerShell**:

```powershell
.\.venv\Scripts\Activate.ps1
```

> 如果 PowerShell 顯示「於此系統上停用了指令碼執行」,請以系統管理員身分開啟 PowerShell 並執行一次:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```

**Command Prompt (cmd)**:

```cmd
.venv\Scripts\activate.bat
```

啟用成功後,命令提示字元前面會出現 `(.venv)` 前綴。

### 4. 升級 pip 並安裝依賴

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` 是完整鎖定版本(top-level + 所有 transitive),所以裝出來的環境跟其他開發者、CI、Production 完全一致。

### 5. 設定 `.env`

```powershell
copy .env.example .env
notepad .env
```

填入真實的:

- `ANTHROPIC_API_KEY`(必填)
- 其他 GCP 相關變數(本機若不連 GCP 資源,可保留範例值)

> ⚠️ `.env` 已在 `.gitignore` 中,**絕對不要** commit 含真實 key 的 `.env`。

### 6. 驗證安裝

```powershell
# 確認所有套件都裝對版本
pip check

# 跑單元測試(不需要 GCP 連線)
pytest tests/unit -v

# 試跑 pre-earnings entry point(會在等 Pub/Sub,Ctrl+C 結束)
python -m pre_earnings.main
```

---

## 退出與重新啟用 venv

退出當前 venv:

```powershell
deactivate
```

下次回到專案時:

```powershell
cd path\to\repo
.\.venv\Scripts\Activate.ps1   # PowerShell
# 或
.venv\Scripts\activate.bat     # cmd
```

---

## macOS / Linux 開發者

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
# 編輯 .env

pytest tests/unit -v
python -m pre_earnings.main
```

---

## 升級依賴的正確流程

`requirements.txt` 是 fully pinned 的,**不要直接手動改 transitive 套件**。要升級時:

1. 編輯 `requirements.txt` **「Top-level」區塊**裡某個套件版本(例如 `anthropic==0.97.0` → `anthropic==0.98.0`)
2. 刪除舊 venv:
   ```powershell
   deactivate
   rmdir /s /q .venv
   ```
3. 重建 venv,**只**安裝 top-level 套件:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   # 暫時手動 install 上面 top-level 區塊列的套件
   pip install google-cloud-storage==3.10.1 google-cloud-pubsub==2.37.0 anthropic==0.98.0 pandas==2.2.3 pyarrow==18.1.0 python-dotenv==1.0.1 pydantic==2.10.4 pytest==8.3.4 pytest-asyncio==0.25.0 pytest-mock==3.14.0 respx==0.22.0
   ```
4. 重新匯出完整 lock:
   ```powershell
   pip freeze > requirements-new.txt
   ```
5. 把 `requirements-new.txt` 的內容貼回 `requirements.txt` 的「Locked transitive」區塊(保留檔頭 comment 和 top-level 區塊),刪掉 `requirements-new.txt`。
6. `pytest tests/unit` 確認沒壞,再 commit。

> 我們刻意不使用 `pip-compile` (pip-tools)。完全鎖定一次性產生即可,不額外引入工具鏈。

---

## 專案結構快覽

```
.
├── common/                  # 共用模組(GCS、Pub/Sub、Claude client、config)
├── pre_earnings/            # pre-earnings workflow
├── ects/                    # earnings call summary workflow
├── tests/                   # unit + integration tests
├── deploy/                  # Dockerfile、K8s manifests
├── configs/                 # per-company config 範例
├── requirements.txt         # 鎖定版本(本檔)
├── .env.example             # 環境變數範例
├── README.md                # 你正在看的這份
└── SDD.md                   # 詳細系統設計文件
```

---

## 常見問題

### Q1. `pip install` 報錯「Microsoft Visual C++ 14.0 or greater is required」

某些套件(`grpcio`、`pyarrow`、`pandas`)的舊版本在 Windows 需要 C++ 編譯工具。本專案 lock 的版本都有預編譯 wheel,理論上不會遇到。如果還是遇到:

- 確認你的 Python 是 64-bit(`python -c "import platform; print(platform.architecture())"`)
- 升級 pip(`python -m pip install --upgrade pip`),舊版 pip 不會找預編譯 wheel

### Q2. `Activate.ps1 cannot be loaded because running scripts is disabled on this system`

PowerShell 的執行策略阻擋了。以系統管理員身分執行 PowerShell 一次:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### Q3. 我裝錯版本的 Python 怎麼辦?

直接刪 venv 重建即可:

```powershell
deactivate
rmdir /s /q .venv
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Q4. 本機開發要連 GCP 嗎?

跑 unit test 不用。Integration test 需要 Pub/Sub emulator(Docker 跑)。完整 end-to-end 需要 GCP 資源。

本機若要呼叫真實 GCP 資源,先執行:

```powershell
gcloud auth application-default login
```

這會在 `%APPDATA%\gcloud\` 寫入 ADC(Application Default Credentials),Python client 會自動使用。Production 上不需要這步,改用 GKE Workload Identity。

### Q5. `pyarrow` 在 Windows 安裝很慢?

`pyarrow` 的 wheel 約 25MB,正常。如果卡在 `Building wheel for pyarrow`,代表你的 pip 沒抓到預編譯 wheel —— 升級 pip 後重裝。

---

## 下一步

- 開發新功能前先讀 [`SDD.md`](./SDD.md) 的對應章節
- 讀過 SDD 第 4 節「Common Modules」後再寫程式碼,避免重複造輪子
- 改 `requirements.txt` 後一定要重新 `pip install -r requirements.txt`,不然其他人 pull 下來會跟你的環境不一致
