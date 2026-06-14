# paper_agent

タイ・ベトナムの**多世代・多文化職場研究**のための、学術論文の
**収集・重複判定・スクリーニング・前処理エージェント**です。

KH Coder によるテキスト分析を前提に、論文 PDF/TXT を取り込み、
重複や不適合・要旨のみ資料を仕分けし、前処理済みテキストと管理台帳
（Excel / CSV / SQLite）を作成します。

> **最終採用の判断は必ず人間が行います。**
> 本ツールは候補を集めて整理・前処理するための研究補助エージェントであり、
> 論文を勝手に採用したり、ファイルを勝手に削除したりしません。
> 重複・除外は **台帳上のフラグ**（`screening_status` / `duplicate_of` など）で管理します。

---

## 1. できること

| 段階 | コマンド | 内容 |
|------|----------|------|
| 初期化 | `init` | フォルダ構成と SQLite DB を作成 |
| 取り込み | `ingest` | フォルダ内の PDF/TXT を取り込み、テキスト抽出・ハッシュ計算 |
| 抽出 | `extract` | 指定論文のテキストを再抽出 |
| 重複判定 | `dedupe` / `dedupe-all` | 3段階で重複・同一データを判定 |
| 採否判定 | `screen` / `screen-all` | 国・職場・世代・本文・カテゴリで採否を判定 |
| 前処理 | `clean` | KH Coder 用に整形した `*_cleaned.txt` を出力 |
| 翻訳準備 | `prepare-translation` | 原文・翻訳プロンプト・翻訳枠・ログを保存 |
| 台帳出力 | `export` | Excel（8シート）/ CSV を出力 |
| メタデータ補完 | `import-metadata` | 人間が編集した Excel/CSV を DB へ反映（空欄は保持） |
| 確認レポート | `report` | 区分別の確認レポート＋Analysis N Summary をコンソール / Markdown 出力 |
| 候補収集 | `harvest` | OpenAlex+Crossref から候補メタデータを収集（**PDFは取得しない**） |
| 承認PDF取得 | `download-approved` | `approved_for_download` の候補だけ合法的に PDF 取得 |
| N集計 | `analysis-n` | 最終的に N に数える論文だけを集計 |

### 分析対象 6 カテゴリ

1. Stereotypes / Ageism（ステレオタイプ・年齢差別）
2. Work Values / Ethics / Motivation（仕事価値観・労働倫理・動機づけ）
3. Knowledge Transfer / Reverse Mentoring / Trust（知識移転・信頼）
4. Communication / Power Distance / Authority（コミュニケーション・権威）
5. Technology Adoption / Change / Digital Transformation（技術受容・変化）
6. Status Quo Bias / Resistance / Retention / Turnover（現状維持バイアス・離職）

---

## 2. セットアップ

Python 3.11 以上が必要です。

```powershell
# Windows PowerShell の例
cd paper_agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

```bash
# macOS / Linux の例
cd paper_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 任意の環境変数

`.env.example` を `.env` にコピーして設定します（秘密情報はコードに直書きしません）。

```
ANTHROPIC_API_KEY=    # あれば LLM 補助・自動翻訳に使用。無くてもルールベースで動作。
CONTACT_EMAIL=        # OpenAlex / Unpaywall の polite pool 用（礼儀）。
OPENALEX_MAILTO=
```

> PDF 抽出は `PyMuPDF`（推奨）または `pdfplumber` のどちらかが入っていれば動作します。
> `langdetect` が無い環境でも、タイ文字・ベトナム語文字のヒューリスティックで言語判定します。

---

## 3. 使い方（基本フロー）

```powershell
# 1. 初期化（data/ サブフォルダと db/papers.sqlite を作成）
python -m paper_agent init

# 2. 既存の PDF/TXT をフォルダごと取り込み（国コード: TH / VN / JP）
python -m paper_agent ingest --input "./data/02_downloaded" --country TH

# 3. 重複チェック（全件）
python -m paper_agent dedupe-all

# 4. 採否判定（全件）
python -m paper_agent screen-all

# 5. Excel 台帳を出力
python -m paper_agent export --format xlsx
```

個別操作:

```powershell
python -m paper_agent extract --paper-id TH-xxxx
python -m paper_agent dedupe  --paper-id TH-xxxx
python -m paper_agent screen  --paper-id TH-xxxx
python -m paper_agent clean   --paper-id TH-xxxx           # KH Coder 用 cleaned.txt
python -m paper_agent prepare-translation --paper-id TH-xxxx
python -m paper_agent export --format csv
python -m paper_agent search --country TH --category 5 --limit 20
```

> パスに日本語が含まれていても処理できます。文字コードはすべて UTF-8 です。
> 途中で 1 件エラーが出ても、全体処理は止まらず次の論文に進みます（ログに記録）。

---

## 3.5 実データ（既存 PDF/TXT）での検証手順

検索（OpenAlex/Unpaywall/ダウンロード）にはまだ進まず、**手元にある既存の
タイ・ベトナム論文 PDF/TXT** を使って、重複判定と採否判定の精度を確認する手順です。

### 手順

1. **ファイルを置く**
   `data/02_downloaded/` 以下に PDF または TXT を置きます。
   国ごとに `--country` を分けたい場合は、国別フォルダに分けると便利です。

   ```
   data/02_downloaded/
   ├── TH/   ← タイの論文 (PDF/TXT)
   └── VN/   ← ベトナムの論文 (PDF/TXT)
   ```

   > KH Coder 用に整形済みの cleaned テキスト（例: `vietnam1_khcoder_cleaned.txt`）も
   > そのまま TXT として取り込めます。`.pdf` / `.txt` / `.text` / `.md` に対応。

2. **一括処理を実行**

   ```powershell
   python -m paper_agent init                                    # 1回だけでOK
   python -m paper_agent ingest --input "./data/02_downloaded/TH" --country TH
   python -m paper_agent ingest --input "./data/02_downloaded/VN" --country VN
   python -m paper_agent dedupe-all
   python -m paper_agent screen-all
   python -m paper_agent export --format xlsx
   python -m paper_agent report                                  # 区分別の確認レポート
   ```

   フォルダを分けない場合は 1 回の `ingest` でも構いません（`--country` は整理用ラベル。
   実際の調査対象国は本文から推定し `target_country` に入ります）。

   > `ingest` はテキスト抽出まで自動で行うため、通常 `extract` を個別に呼ぶ必要はありません。
   > 抽出をやり直したいときだけ `python -m paper_agent extract --paper-id <ID>` を使います。

3. **確認レポートを見る**

   ```powershell
   python -m paper_agent report --format console   # コンソールに要約だけ
   python -m paper_agent report --full             # コンソールに区分別の全一覧
   python -m paper_agent report --format md        # data/10_exports/report.md を出力
   ```

   レポートは次の 6 区分（＋翻訳が必要な論文一覧）で出力されます。

   - **完全重複** … DOI / 本文ハッシュ / 正規化タイトル一致など
   - **同一データの可能性あり** … タイトルは違うが同一研究の疑い（要人手確認）
   - **主分析候補（accepted）**
   - **補助文献（supplementary）** … 単一世代 / 要旨のみ / Teaching Case など
   - **除外（rejected）**
   - **要確認（needs_review）** … タイ語/ベトナム語原文・Results/Discussion 混在など

### 判定の確認ポイント（誤判定が起きないこと）

実データで以下が守られているかをレポート/Excel で確認してください。

| 確認項目 | 期待される区分 |
|----------|----------------|
| 単一世代研究（Gen Z のみ等） | 主分析に入らず **補助文献** |
| 要旨のみ（本文なし・学会要旨） | 主分析に入らず **補助文献** |
| Teaching Case / 授業用ケース | 主分析に入らず **補助文献** |
| タイ語・ベトナム語の原文 | **要確認**（`original_language=th/vi`、翻訳が必要な論文一覧に掲載） |
| 既知の重複ペア | **完全重複** または **同一データの可能性あり** |

> タイ語/ベトナム語の原文は、英語キーワードでは職場文脈やカテゴリを正しく判定できないため、
> 誤って除外せず **needs_review（翻訳要）** に回します。英訳テキストを
> `09_translated/<paper_id>/translated_en.txt` に入れて再取り込みすれば、英語として本判定できます。

### メタデータについて（重要）

`dedupe-all` を `screen-all` の前に実行する標準フローでは、取り込み直後の段階で
**対象国・世代区分・言語** は本文から自動補完されます。一方、**著者名・標本数** は
PDF から確実に取り出すのが難しいため自動では埋めません（推測しない方針）。

「**同一データの可能性あり**（学位論文⇔雑誌版など、タイトルが違う同一研究）」の検出は、
著者名・標本数が入っていると精度が大きく上がります。これらは次節の
`import-metadata` で Excel から補ってから `dedupe-all` を再実行すると、より確実に
検出できます。完全重複・版違い（同一タイトル/同一本文）はメタデータが無くても検出されます。

---

## 3.6 実データ投入後、Excel でメタデータを補完して再判定する手順

PDF から **著者名・標本数・DOI・調査対象** などは自動では確実に取れないため
（推測しない方針で空のまま）、人間が Excel で補ってから再判定する運用を用意しています。

### 流れ

```powershell
# 1) いったん台帳を出力（取り込み済みの全件が All_Papers シートに並ぶ）
python -m paper_agent export --format xlsx

# 2) data/10_exports/paper_inventory.xlsx を Excel で開き、必要な列を手入力で補完する
#    （下表の列が編集対象。空欄のままにした列は既存値を保持＝消えない）

# 3) 編集した Excel を取り込んで DB を更新
python -m paper_agent import-metadata --input "./data/10_exports/paper_inventory.xlsx"

# 4) 補完後のメタデータで再判定
python -m paper_agent dedupe-all
python -m paper_agent screen-all
python -m paper_agent report --full
python -m paper_agent export --format xlsx
```

> CSV でも取り込めます（`export --format csv` で出した `paper_inventory.csv` を編集 → `import-metadata --input ...csv`）。
> Excel が付ける UTF-8 BOM 付き CSV にも対応しています。

### 編集（取り込み）対象の列

`All_Papers` シートの **paper_id をキー**に、以下の列だけを更新します。
`screening_status` などの判定結果列は手入力しても取り込まれません（再判定で決まります）。

| Excel 列 | 内容 |
|----------|------|
| `title` | タイトル（修正すると正規化タイトルも自動更新） |
| `authors` | 著者（`;` 区切り。姓で重複判定に使う） |
| `year` | 出版年（整数） |
| `doi` | DOI |
| `country` | 整理用の国ラベル（TH/VN/JP） |
| `category` | カテゴリ（category_1〜category_6） |
| `target_country` | **調査対象国**（Thailand/Vietnam。著者所属国ではない） |
| `organization_context` | 職場文脈（例: SME, hospital, hospitality, family business） |
| `generation_groups` | 世代区分（例: `gen_x; gen_y`） |
| `number_of_generations` | 比較した世代数（整数） |
| `sample_size` | 標本数（整数） |
| `method`（=research_method） | 研究手法（例: survey, interview, mixed） |
| `document_type` | journal_article / thesis / conference_paper / conference_abstract / teaching_case / report / unknown |
| `original_language` | 原文言語（en/th/vi） |
| `analysis_language` | 分析言語（通常 en） |
| `notes` | 備考 |

### 取り込みの約束

- **空欄は既存値を上書きしません**（補完であって消去ではない）。値を消したい場合は DB を直接編集してください。
- DB に存在しない `paper_id` の行はスキップします（警告表示）。
- `number_of_generations` / `sample_size` / `year` に数値以外、`document_type` に未知の値が入っていた行は、その列だけ無視して警告します（処理は止まりません）。
- とくに **`authors` と `sample_size`（必要なら `organization_context`・`research_method`）を埋める**と、
  「同一データの可能性あり」（タイトルが違う学位論文⇔雑誌版など）の検出精度が上がります。

---

## 3.7 Windows PowerShell まとめ（実データ検証チートシート）

> 前提: `cd paper_agent` でプロジェクト直下にいること（`config/` `data/` `db/` が見える場所）。
> 仮想環境を使う場合は `.\.venv\Scripts\Activate.ps1` を先に実行。

### A. 既存DB・出力ファイルを安全にリセット

`data/02_downloaded/`（あなたが入れた PDF/TXT）は消えません。生成物だけを消します。

```powershell
# 管理台帳(DB)をリセット
Remove-Item -Force -ErrorAction SilentlyContinue .\db\papers.sqlite

# 生成物をリセット（.gitkeep は残す）
Get-ChildItem .\data\03_screening, .\data\08_cleaned, .\data\10_exports `
  -File -Exclude '.gitkeep' -ErrorAction SilentlyContinue | Remove-Item -Force
Get-ChildItem .\data\09_translated -Directory -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .\logs

# 初期化（フォルダとDBを作り直す）
python -m paper_agent init
```

> `02_downloaded` の中身も入れ替えてやり直したいときだけ、次も実行:
> `Get-ChildItem .\data\02_downloaded -File -Exclude '.gitkeep' | Remove-Item -Force`

### B. 実データの一括処理（取り込み→判定→レポート→台帳）

`data/02_downloaded/` に PDF/TXT を入れてから:

```powershell
python -m paper_agent init
python -m paper_agent ingest --input "./data/02_downloaded" --country TH
python -m paper_agent dedupe-all
python -m paper_agent screen-all
python -m paper_agent report --full
python -m paper_agent export --format xlsx
```

> 国を分けたいときは `data/02_downloaded/TH` と `data/02_downloaded/VN` に分け、
> `ingest` を国ごとに2回実行（`--country TH` / `--country VN`）。`--country` は整理用ラベルで、
> 実際の調査対象国は本文から推定され `target_country` に入ります。

### C. Excel でメタデータを補完してから再判定

`data/10_exports/paper_inventory.xlsx` を開いて `authors` / `sample_size` などを手入力 → 保存 → 取り込み:

```powershell
python -m paper_agent import-metadata --input "./data/10_exports/paper_inventory.xlsx"
python -m paper_agent dedupe-all
python -m paper_agent screen-all
python -m paper_agent report --full
python -m paper_agent export --format xlsx
```

### D. （任意）採用論文を KH Coder 用 cleaned.txt にする

cleaned テキストは `clean` コマンドで論文ごとに生成します（採用が確定した論文に対して実行）。
採用（accepted）の paper_id をまとめて処理する例:

```powershell
$ids = python -c "from paper_agent.db import PaperDB; [print(r.paper_id) for r in PaperDB().by_status('accepted')]"
foreach ($id in $ids) { python -m paper_agent clean --paper-id $id }
```

出力は `data/08_cleaned/<paper_id>_..._cleaned.txt`（UTF-8）です。

### E. 実データ検証で「どのファイルを見るか」

| 見るもの | 場所 | 何を確認するか |
|----------|------|----------------|
| **確認レポート** | `data/10_exports/report.md` | 6区分の全体像。まずここを見る |
| **管理台帳** | `data/10_exports/paper_inventory.xlsx` | 全件の詳細。シートで区分別に確認（下記） |
| → 主分析候補 | xlsx の `Accepted` シート | 採用候補。最終採用は人間が決める |
| → 補助文献 | xlsx の `Supplementary` シート | 単一世代/要旨のみ/Teaching Case など |
| → 除外 | xlsx の `Rejected` シート | 対象国不一致・職場文脈なし等 |
| **重複の確認** | xlsx の `Duplicates` シート / report.md「完全重複」 | `duplicate_of` がどの論文を指すか |
| **要確認** | xlsx の `Needs_Review` シート / report.md「要確認」「同一データの可能性あり」 | タイ語原文・章混在・同一データ疑い |
| **翻訳が必要な論文** | report.md 末尾の一覧 | `original_language=th/vi` の論文 |
| **KH Coder 入力** | `data/08_cleaned/*_cleaned.txt` | `clean` 実行後に生成（手順D） |
| **翻訳準備** | `data/09_translated/<paper_id>/` | `prepare-translation` 実行後 |
| **ログ** | `logs/paper_agent.log` | 抽出失敗など処理中の警告 |

> ツールはファイルを削除しません。重複・除外は台帳のフラグ（`screening_status` /
> `duplicate_of`）で管理されるので、元の PDF/TXT は `02_downloaded` に残ります。

---

## 3.8 候補収集 (harvest) と N カウントの運用

「ルールに合いそうな論文を広く候補として集める」一方で、**N（analysis_N）に数えるのは
最終的に accepted になり重複もない論文だけ**、という運用です。

### 全体の流れ

```text
harvest で候補を集める（PDFは取得しない）
  ↓
候補メタデータを candidates に保存（候補段階の重複チェック込み）
  ↓
export → Excel の Candidates シートを人間が確認
  ↓
良さそうな候補だけ candidate_status = approved_for_download にする
  ↓
import-metadata で Excel の編集を DB に反映
  ↓
download-approved で承認済み候補だけ PDF 取得（合法なOAのみ）
  ↓
ingest → dedupe-all → screen-all（通常処理）
  ↓
accepted かつ duplicate_of 空・same_dataset_warning=false のものだけ N
```

### PowerShell コマンド

```powershell
# 1) 候補収集（PDFは取得しない。candidate_score は優先順位付け用で自動採用しない）
python -m paper_agent harvest --country TH --category 5 --limit 100
python -m paper_agent harvest --country VN --category 5 --limit 100

# 2) 候補確認用 Excel 出力
python -m paper_agent export --format xlsx

# 3) data/10_exports/paper_inventory.xlsx の「Candidates」シートを開き、
#    良さそうな候補だけ candidate_status を approved_for_download に変更して保存

# 4) Excel の候補ステータスを DB へ反映
python -m paper_agent import-metadata --input "./data/10_exports/paper_inventory.xlsx"

# 5) 承認済み候補だけ PDF 取得（合法的な OA のみ。違法・不明確はスキップ）
python -m paper_agent download-approved

# 6) PDF 取得後の通常処理
python -m paper_agent ingest --input "./data/02_downloaded/TH" --country TH
python -m paper_agent ingest --input "./data/02_downloaded/VN" --country VN
python -m paper_agent dedupe-all
python -m paper_agent screen-all
python -m paper_agent report --full
python -m paper_agent export --format xlsx

# 7) N を確認
python -m paper_agent analysis-n
```

> `harvest` はネットワークが必要です（OpenAlex/Crossref）。`.env` に `CONTACT_EMAIL` を
>設定しておくと API の polite pool を使えます。ネットワークが無い場合は候補0件で安全に終了します。

### N に数える条件（厳守）

`analysis-n` / `report --full` の **Analysis N Summary** は、以下を **すべて** 満たす
PaperRecord だけを N に数えます。

```text
screening_status == accepted
duplicate_of is empty
same_dataset_warning == false
full_text_available == true
number_of_generations >= 2
target_country is Thailand or Vietnam
workplace_fit == true
```

**N に数えないもの**（重要）:

- harvest で見つけただけの候補 / pdf_url が見つかっただけの候補 / PDF を保存できただけの論文
- duplicate / probable_duplicate / same_dataset_possible / needs_review / supplementary / rejected
- conference_abstract / teaching_case
- 単一世代研究（Gen Zのみ等）/ 要旨のみ / 本文なし
- 対象国がタイ・ベトナムでない / 職場文脈がない

> **harvest 件数も download 件数も N ではありません。** PDF が取れても自動採用しません。

### N はどこで確認するか

| 見るもの | 場所 |
|----------|------|
| **N の合計と内訳** | `python -m paper_agent analysis-n`（コンソール） |
| 同じ内容＋区分別 | `data/10_exports/report.md` の「Analysis N Summary」 |
| **N に入った論文一覧** | `paper_inventory.xlsx` の `Analysis_N` シート |
| 候補の一覧・状態 | `paper_inventory.xlsx` の `Candidates` シート |
| 候補の重複 | `paper_inventory.xlsx` の `Candidate_Duplicates` シート |
| 候補の要確認 | `paper_inventory.xlsx` の `Candidate_Needs_Review` シート |

### 候補ステータス (`candidate_status`)

```text
pending               収集直後。人間未確認
approved_for_download 人間が承認。download-approved の対象
rejected              人間が除外
duplicate             候補段階で既存と完全/probable 重複
needs_review          候補段階で same_dataset 疑い等（自動除外しない）
downloaded            PDF 取得済み
screened              （将来用）
```

`download-approved` は **`candidate_status == approved_for_download`** かつ
**`pdf_url` が空でない** かつ **`legality_note` が違法・不明確でない** 候補だけを対象にします。
取得できない場合は `download_status` / `download_error` / `attempted_url` / `timestamp` を
記録して次へ進みます（処理は止まりません）。

### Candidates シートで編集できる列

`import-metadata` は `Candidates` シートを `candidate_id` をキーに取り込みます（空欄は保持）。
編集できる列: `candidate_status` / `notes` / `category` / `target_country` /
`generation_keywords` / `workplace_keywords` / `document_type_guess` /
`open_access_flag` / `pdf_url`。

---

## 3.9 autopilot (エージェント型自律ワークフロー)

毎回 Excel を1件ずつ確認しなくても、候補収集→重複判定→採否→PDF取得→前処理→
N カウント→品質検査までを自律的に回し、**重複なし・ルール適合・主分析に使える
accepted 論文** をできるだけ多く（目標 100 本）確保します。
**100 本に届かない場合は水増しせず、何本確保できたか・なぜ未達かを正直に報告します。**

### 役割エージェント (コード設計上の分担)

`paper_agent/agents/` に12の役割を分離しています（Claude の別インスタンスを生成する
わけではなく、責務を明確にした設計）。SearchPlanner / Harvester / CandidateScreening /
DuplicateCheck / LegalAccess / Download / FullTextScreening / MetadataExtraction /
Cleaner / AnalysisN / **QualityAssurance** / Supervisor。

### コマンド

```powershell
# まずは必ず dry-run で品質を確認（PDFは取得しない。候補収集・候補重複・スコアリングまで）
python -m paper_agent autopilot --target-n 100 --countries TH,VN --categories 1,2,3,4,5,6 --per-query-limit 50 --dry-run --qa-strict

# dry-run の結果が良ければ通常実行（合法なOA PDFのみ自動取得 → ingest → screen → N）
python -m paper_agent autopilot --target-n 100 --countries TH,VN --categories 1,2,3,4,5,6 --per-query-limit 50

# オプション
python -m paper_agent autopilot --target-n 100 --max-rounds 10
python -m paper_agent analysis-n          # N だけ確認
```

- `--dry-run`: PDF を取得せず、候補収集・候補重複判定・候補スコアリング・search_history 保存まで。
- `--qa-strict`: 品質検査で不整合があれば表示し、該当を N から除外（処理は止めない）。

### ラウンドの流れ（Supervisor が統括）

```
SearchPlanner → Harvester → CandidateScreening → DuplicateCheck(候補) →
LegalAccess → 高信頼候補だけ自動承認 → Download → ingest →
DuplicateCheck(論文) → FullTextScreening → MetadataExtraction →
Cleaner(accepted) → AnalysisN → QualityAssurance → Supervisor が継続/停止を判断
```

エラーが出てもログに残して次へ進みます。**停止条件**: target_N 到達 / 検索空間を
尽くした / 連続3ラウンド新規 accepted が0 / max_rounds 到達。停止時は必ず理由を出します。

### 自動承認の条件（section 5 準拠）

`candidate_score >= 0.6` かつ `duplicate_status == new_candidate` かつ対象国 TH/VN かつ
多世代シグナルあり かつ workplace_keywords あり かつ pdf_url あり かつ
legality が open access / official-repository かつ teaching_case/conference_abstract でない、
を**すべて**満たす候補だけ。`same_dataset_possible` / `probable_duplicate` /
`needs_review` は自動承認しません。

### QualityAssuranceAgent（最重要）

各ラウンド後に20項目を検査し、**N に不適合な accepted を自動で needs_review に降格して
N から除外**します（重複/同一データ/単一世代/要旨のみ/teaching_case/conference_abstract/
対象国不一致/職場文脈なし/本文なし、cleaned・metadata の有無、DOI/source_url の有無、
同一 DOI・タイトル・データ署名が複数 accepted になっていないか 等）。結果は `qa_report.md`。

### query_country と target_country の分離（重要）

検索で指定した国（`query_country`）と、論文が実際に扱う調査対象国（`target_country`）を
厳密に分けています。**検索クエリに Thailand と入っているだけでは `target_country=Thailand`
にしません。** タイトル・要旨・本文・候補メタデータの**証拠**で確認できた場合のみ採用します。

- `target_country_source`: `title` / `abstract` / `full_text` / `metadata` / `query_only` / `unknown`
- `target_country_evidence`: 根拠（本文中の語など）
- **`query_only` / `unknown` は N に入れない・auto-approve しない・国スコアに加点しない。**

Candidates シートの `target_country_source` / `target_country_evidence` /
`auto_approve_blockers`（承認されなかった理由）/ `download_status` を見れば、
なぜ承認・除外されたかを1件ずつ追えます。

### N に数える条件（厳守・AnalysisNAgent が唯一の責任者）

```text
screening_status == accepted / duplicate_of is empty / same_dataset_warning == false
full_text_available == true / number_of_generations >= 2
target_country is Thailand or Vietnam
target_country_source in title/abstract/full_text/metadata   ← query由来は不可
target_country_evidence is not empty
workplace_fit == true
document_type is not teaching_case / not conference_abstract
```

**harvest 件数・download 成功件数・candidate 件数は絶対に N に数えません。**

### download_attempted=0 のときの診断

`autopilot_summary.md` には `download_attempted_count` と、0 の場合の理由内訳
（`target_country_source=query_only` / `pdf_url_missing` / `legality_unknown` /
`generation_evidence_insufficient` / `workplace_evidence_missing` /
`candidate_score<...` 等）を出します。`download_status` 別件数・`candidate_status` 別件数・
`target_country_source` 別件数も出るので、「なぜ download が走らなかったか」を summary だけで判断できます。

### 出力ファイル（autopilot 終了後）

| ファイル | 内容 |
|----------|------|
| `data/10_exports/autopilot_summary.md` | 最終 N・国×カテゴリ別 N・harvest/download 数・停止理由・N一覧・未達理由 |
| `data/10_exports/qa_report.md` | QA 20項目の検査結果と降格一覧 |
| `data/10_exports/paper_inventory.xlsx` | 全シート（`Analysis_N` / `Candidates` ほか） |
| `data/10_exports/report.md` | 区分別レポート＋Analysis N Summary |
| `data/10_exports/accepted_for_analysis.csv` | N に入った論文だけ |
| `data/10_exports/rejected_reasons.csv` | rejected/supplementary/needs_review の理由 |
| `data/10_exports/duplicate_groups.csv` | duplicate_of の対応 |
| `data/10_exports/search_history.csv` | 実行済み検索クエリ |

> **N の確認**: まず `autopilot_summary.md` の「最終 accepted N」、詳細は `Analysis_N` シートと
> `accepted_for_analysis.csv`。なぜ未達かは `autopilot_summary.md` の「N に入らなかった主な理由」と
> `qa_report.md`。

### 自己検証（推奨手順）

```powershell
# 1) 小さく
python -m paper_agent autopilot --target-n 10 --countries TH,VN --categories 1,2 --per-query-limit 10 --dry-run --qa-strict
python -m paper_agent analysis-n
# 2) 全カテゴリ dry-run
python -m paper_agent autopilot --target-n 10 --countries TH,VN --categories 1,2,3,4,5,6 --per-query-limit 20 --dry-run --qa-strict
python -m paper_agent export --format xlsx
# 3) 本番規模 dry-run（品質確認後に PDF 取得ありへ）
python -m paper_agent autopilot --target-n 100 --countries TH,VN --categories 1,2,3,4,5,6 --per-query-limit 50 --dry-run --qa-strict
```

> `autopilot` はネットワーク（OpenAlex/Crossref）が必要です。`.env` に `CONTACT_EMAIL` を
> 設定すると API の polite pool を使えます。ネットワークが無いと候補0件で安全に終了します。

---

## 4. フォルダ構成

```
paper_agent/
├── README.md
├── requirements.txt
├── .env.example
├── config/
│   ├── screening_rules.yaml      # 国・職場・世代・文書タイプ・本文/言語ルール
│   ├── categories.yaml           # 6カテゴリのキーワード
│   └── translation_glossary.yaml # 翻訳用語集
├── data/
│   ├── 01_candidates/   # 検索で得た候補
│   ├── 02_downloaded/   # 取得した PDF/TXT
│   ├── 03_screening/    # 抽出テキスト
│   ├── 04_accepted/     # 採用（人間が確認後に移動可）
│   ├── 05_supplementary/# 補助文献
│   ├── 06_rejected/     # 除外
│   ├── 07_duplicates/   # 重複
│   ├── 08_cleaned/      # KH Coder 用 cleaned.txt
│   ├── 09_translated/   # 翻訳準備一式
│   └── 10_exports/      # Excel / CSV 台帳
├── db/
│   └── papers.sqlite    # 管理台帳（実行時に生成）
├── paper_agent/         # Python パッケージ本体
│   ├── cli.py           # CLI
│   ├── models.py        # Pydantic データモデル
│   ├── db.py            # SQLite ストア
│   ├── extractor.py     # PDF/TXT 抽出・章分割
│   ├── cleaner.py       # KH Coder 用クリーニング
│   ├── duplicate_checker.py  # 3段階の重複判定
│   ├── screener.py      # ルールベース採否判定
│   ├── translator_prep.py    # 翻訳準備
│   ├── exporter.py      # Excel/CSV 出力
│   ├── search_openalex.py    # OpenAlex 検索
│   ├── search_unpaywall.py   # Unpaywall OA 確認
│   ├── downloader.py    # OA PDF ダウンロード
│   └── utils.py         # 正規化・ハッシュ・言語判定など
└── tests/
    ├── test_duplicate_checker.py
    ├── test_screening_rules.py
    └── test_filename_normalizer.py
```

> `data/` 配下の生成物と `db/papers.sqlite` は `.gitignore` 済みです。
> PDF バイナリ（`*.pdf`）はコミットされません（権利・容量・合法性のため）。

---

## 5. 重複判定の仕組み（3段階）

`duplicate_checker.py` が次の 3 段階で判定します。

- **Stage 1 — 完全一致（`exact_duplicate`）**
  DOI 一致 / PDF の SHA256 一致 / 正規化タイトル一致 / 抽出本文ハッシュ一致。
  正規化タイトルは「小文字化・記号除去・コロン/ハイフン/引用符の差の吸収」を行い、
  `Thai` `Vietnam` `workplace` などの語は保持します。

- **Stage 2 — 高類似（`probable_duplicate`）**
  RapidFuzz でタイトル類似度を計算し、タイトルが高類似 **かつ**
  著者・標本数・対象国のいずれかが一致する場合。

- **Stage 3 — 同一データ・改題版（`same_dataset_possible`）**
  タイトルは違っても、著者・対象国・業界・標本数・世代区分・手法が重なる場合。
  学位論文⇔雑誌版、会議版⇔ジャーナル版の可能性。
  **自動除外せず `needs_review`** にして人間に委ねます。

判定結果は台帳に `screening_status` / `duplicate_of` / `duplicate_confidence` /
`same_dataset_warning` として記録され、**ファイルは削除されません**。

---

## 6. 採否判定の観点

`screener.py` が次の観点で `accepted / supplementary / rejected / needs_review` を判定します。

1. **Country fit** — 調査対象国が Thailand / Vietnam か（著者所属国ではなく**調査文脈**を重視）
2. **Workplace fit** — 企業・大学・公共組織・家族企業などの職場文脈があるか
3. **Generation fit** — 2世代以上を実際に比較しているか（単一世代研究は主分析から除外）
4. **Full text fit** — Abstract だけでなく本文（分析対象章）が取得できるか
5. **Category fit** — 6カテゴリのどれに最も近いか（primary / secondary）
6. **Document type** — journal / thesis / conference / teaching_case など
7. **Language** — タイ語・ベトナム語本文なら `translation_required=True`

例（採用）:

```
decision: accepted
confidence: 0.86
country_fit: true / workplace_fit: true / generation_fit: true / fulltext_fit: true
category_fit: category_5
reasons:
  - Thailand is the research context.
  - The study compares Baby Boomers, Gen X, Gen Y, and Gen Z.
warnings:
  - Original full text is Thai; English translation is required before KH Coder analysis.
```

### 抽出対象とする章 / 除外する章

- **残す**: Abstract / Introduction / Literature Review / Theoretical Background /
  Discussion / Conclusion / Summary / Implication / Limitation / Future Research
- **除外**: Methods / Methodology / Survey / Questionnaire / Measurement /
  Data Analysis / Results / Findings / References / Appendix / Acknowledgement / Author Bio

> Results / Findings は原則除外。ただし Discussion と混在している場合は `needs_review` にします。

---

## 7. KH Coder 用クリーニング

`clean` コマンドは、分析対象章のみを連結し、ヘッダー/フッター・ページ番号・
参考文献・URL/DOI を除去し、段落を保持したまま UTF-8 で保存します。

出力ファイル名例:

```
TH-5-04_2026_Goerlich_Navigating-Generational-Diversity_cleaned.txt
```

---

## 8. 翻訳準備

`prepare-translation` は自動翻訳を必須にしません。まず翻訳対象テキストを抽出し、
固定の翻訳プロンプト + 用語集とともに保存します。

```
data/09_translated/<paper_id>/
├── original_extracted.txt   # 抽出した原文
├── translation_prompt.txt   # 翻訳指示（faithful translation / 世代名は原文保持 など）
├── translated_en.txt        # 翻訳結果（人手 or API で埋める）
└── translation_log.json     # 処理ログ
```

`--translate` を付け、`ANTHROPIC_API_KEY` と `anthropic` パッケージがあれば自動翻訳します。

---

## 9. 台帳（Excel）

`export --format xlsx` で `data/10_exports/paper_inventory.xlsx` を出力します。
シートは次の 8 種類です。

`All_Papers` / `Accepted` / `Supplementary` / `Rejected` / `Duplicates` /
`Needs_Review` / `Category_Counts` / `Country_Category_Crosstab`

---

## 10. 検索（Phase 3 / 任意）

`search` は **OpenAlex API** を使い、候補を取得して `candidate` として保存します。
**検索結果を自動採用しません。** 必ず `screen` / 人間確認を通します。

合法的に取得可能な公開 API のみを使用し、以下は**行いません**:
Google Scholar の大量スクレイピング / paywall 回避 / Sci-Hub 等の利用 /
robots.txt・利用規約違反のアクセス / 認証が必要な PDF の無断取得。

---

## 11. テスト

```bash
python -m pytest tests/ -q
```

- `test_duplicate_checker.py` … DOI / タイトル / ハッシュ一致、同一データ警告
- `test_screening_rules.py` … 単一世代除外・要旨のみ除外・Teaching Case・タイ語翻訳要否
- `test_filename_normalizer.py` … タイトル正規化（コロン/ハイフン/引用符差の吸収）

サンプルデータが無くても実行できます。

---

## 12. 設計上の約束

- 不明な情報は推測せず `unknown` のままにします。
- 研究対象国と著者所属国を混同しません。
- PDF 取得に失敗した場合は失敗理由を記録します。
- Abstract しかない場合は `full_text_available=False` にします。
- 既存ファイルと重複しても削除せず、`duplicate_of` を記録します。
- 文字コードは UTF-8、Windows PowerShell でも動作します。
- ログは `logs/paper_agent.log` に残ります。
