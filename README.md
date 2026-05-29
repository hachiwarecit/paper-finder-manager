# paper-finder-manager

卒業研究で使用する論文候補を収集・管理するための文献管理システム。

## 目的

研究テーマ「多世代・多文化職場における心理的障壁を、既存論文のテキスト分析によって整理する研究」のために、
**Thailand / Vietnam / Japan** の3か国について、6カテゴリごとに論文候補を検索・整理・管理する。

このシステムが自動化するのは次の範囲:

1. 論文候補の検索
2. メタデータ取得
3. Open Access PDF の取得
4. 国・カテゴリ・採否・理由の管理
5. 重複チェック
6. CSV / JSON での候補表出力

> 論文の **最終採用判断は人間が行う**。本システムは「候補を集め、表に整理し、PDF 取得可能性を管理する」ためのもの。

### 分析カテゴリ

| # | カテゴリ |
|---|----------|
| 1 | Stereotypes / Ageism |
| 2 | Work Values / Work Ethic |
| 3 | Knowledge Transfer / Mentoring |
| 4 | Communication / Psychological Safety |
| 5 | Change Adaptation / Technology Adoption |
| 6 | Status Quo Bias / Resistance to Change |

著者の所属国ではなく、**論文が実際に扱っている対象国・対象地域・対象組織文脈**を重視する。

## データソース

公開 API のみを使用する:

- [OpenAlex API](https://docs.openalex.org/)
- [Crossref REST API](https://api.crossref.org/)
- [Semantic Scholar API](https://api.semanticscholar.org/)
- [Unpaywall API](https://unpaywall.org/products/api)

MVP では **OpenAlex + Unpaywall** を優先する。Crossref / Semantic Scholar 用のモジュール
(`scripts/search_crossref.py`, `scripts/search_semantic_scholar.py`) も同じ正規化フォーマットで
用意済みで、後から本採用できる。

> **Google Scholar や出版社サイトの HTML スクレイピングは行わない。**
> PDF の自動取得は、Open Access で合法的に取得可能な PDF に限定する。

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

API への礼儀として、連絡先メールを環境変数で指定する（Unpaywall は **email が必須**）:

```bash
export OPENALEX_MAILTO="you@example.com"
export UNPAYWALL_EMAIL="you@example.com"
# 任意
export CROSSREF_MAILTO="you@example.com"
export SEMANTIC_SCHOLAR_API_KEY="..."
```

## 実行方法

```bash
# 単一の 国 × カテゴリ
python scripts/run_search_pipeline.py \
  --country Thailand \
  --category "Status Quo Bias / Resistance to Change" \
  --limit 20

# 1か国の全カテゴリ
python scripts/run_search_pipeline.py \
  --country Thailand \
  --all-categories \
  --limit 20

# 3か国すべて × 全カテゴリ
python scripts/run_search_pipeline.py \
  --all-countries \
  --all-categories \
  --limit 20
```

主なオプション:

- `--limit N` : クエリごとの取得件数上限（既定 20）
- `--no-download` : OA PDF のダウンロードをスキップ
- `--mailto` / `--email` : OpenAlex / Unpaywall 用メール

個別スクリプトも単体実行できる（デバッグ用）:

```bash
python scripts/search_openalex.py "psychological safety workplace Japan" --limit 5
python scripts/check_unpaywall.py 10.1016/j.example.2020.01.001
python scripts/export_tables.py   # 既存 candidates.json からレポートを再生成
```

## 出力

| ファイル | 内容 |
|----------|------|
| `data/candidates.csv` | 候補表（全列） |
| `data/candidates.json` | 候補表（JSON、再実行時のマージ元） |
| `reports/candidate_table.csv` | 人間レビュー用の要約表（重複・除外を除く） |
| `reports/missing_slots.csv` | 採用論文がまだ無い枠（3か国 × 6カテゴリ × 2本） |
| `reports/duplicates.csv` | 重複と判定された候補 |
| `pdfs/{country}/...` | OA PDF（取得できた場合のみ） |
| `organized_pdfs/{country}/...` | 整理済み PDF のコピー（`organize_pdfs.py`、後述） |
| `reports/rename_log.csv` | PDF 整理の実行ログ（`organize_pdfs.py`） |
| `khcoder_ready/{country}/{paper_id}/...` | KH Coder 用に前処理したテキスト一式（`preprocess_pdfs_for_khcoder.py`、後述） |
| `reports/khcoder_preprocess_log.csv` | KH Coder 前処理のまとめログ |

> 生成ファイルと PDF バイナリは `.gitignore` 済み。実行のたびに再生成される。

## `candidates.csv` の列

| 列 | 説明 |
|----|------|
| `id` | 決定論的ID（DOI かタイトルから生成。再実行でマージされる） |
| `country` | 検索対象の国（Thailand / Vietnam / Japan） |
| `category` | 6カテゴリのいずれか |
| `slot` | 枠。`{country}_{paper_no}-{category_index}`（例 `Thailand_2-6`）。手動指定可 |
| `title` | 論文タイトル |
| `authors` | 著者（`; ` 区切り） |
| `year` | 出版年 |
| `journal_source` | 掲載誌・ソース名 |
| `doi` | DOI |
| `url` | ランディングページ URL |
| `pdf_url` | OA PDF の URL（あれば） |
| `oa_status` | Open Access 状態（gold / green / hybrid / closed 等） |
| `target_country_region` | title/abstract で確認できた対象国・地域語 |
| `research_context` | 検出した職場・組織文脈の語 |
| `abstract` | 要旨 |
| `related_category` | カテゴリキーワードの一致数・一致語 |
| `reason_for_inclusion` | 自動判定の根拠サマリ |
| `screening_status` | スクリーニング状態（下表） |
| `local_pdf_path` | 取得した PDF のローカルパス |
| `notes` | 自由記述（人間が編集） |
| `duplicate_of` | 重複の場合、原本の `id` |
| `created_at` / `updated_at` | 作成・更新時刻（ISO8601 UTC） |

### `screening_status` の種類

| ステータス | 意味 |
|------------|------|
| `candidate` | 候補 |
| `strong_candidate` | 有力候補（対象国・職場文脈・カテゴリ一致が揃い OA PDF あり） |
| `needs_review` | 要レビュー（対象国文脈が不明、またはカテゴリ不一致など） |
| `adopted` | 採用（人間が決定） |
| `rejected` | 不採用（人間が決定） |
| `exclude_country_mismatch` | 対象国が一致しない |
| `exclude_no_workplace_context` | 職場・組織文脈が無い |
| `exclude_no_abstract_or_pdf` | abstract も PDF も取得できない |
| `manual_download_needed` | 条件は満たすが OA PDF が無く手動取得が必要 |
| `duplicate` | 重複 |

> `adopted` / `rejected` / `needs_review` など人間が決めたステータスは、
> 再実行時のマージで**上書きされない**。

### `slot` の考え方

各国 × カテゴリごとに 1本目・2本目を管理する（例 `Thailand_2-6` = 2本目・カテゴリ6）。
パイプラインは上位候補に**暫定 slot** を自動割り当てするが、最終決定は人間が行い、手動指定もできる。

`reports/missing_slots.csv` には、まだ採用論文（`adopted` / `strong_candidate`）が無い枠が出力される。

## 自動判定ルール（スコアベース）

`rules/` 配下の YAML で調整できる:

- `rules/country_keywords.yml` … 対象国判定の語
- `rules/category_keywords.yml` … 6カテゴリのキーワード
- `rules/inclusion_rules.yml` … 組織文脈語・医学/物理環境の除外語・スコア閾値・格上げ条件

候補ごとに **4 つのスコア**を計算する（candidates.csv に列として記録）:

| スコア | 意味 |
|--------|------|
| `country_score` | 対象国・対象文脈の語の一致数（著者所属だけでは判定しない） |
| `workplace_organization_score` | 組織・人・心理・世代・文化の語の一致数 |
| `category_score` | 6カテゴリのキーワード一致数 |
| `exclusion_medical_score` | 医学/公衆衛生 + 物理的作業環境の語の一致数 |

### 医学・物理環境論文の除外

`workplace` という語があるだけでは職場組織文脈とみなさない。本研究の対象は
「物理的な作業環境」ではなく「**職場組織・従業員・チーム・マネジメント・心理的障壁**」である。
そのため次のように判定する。

- **`exclusion_medical_score` が高く（既定 ≥2）、`workplace_organization_score` が低い（<2）** 候補は
  対象国や `workplace` 語が含まれていても **`exclude_no_workplace_context`** にする。
  - 例: 感染症・疫学・公衆衛生論文（disease, infection, patient, hospital, clinical,
    case-control, PCR, ELISA, epidemiology, odds ratio, diagnosis, mortality,
    public health, scrub typhus など）
  - 例: `workplace environment` が forest / hilly field / water bodies / bushes /
    agricultural field / infection risk / occupational exposure など物理的環境を指す場合
- 医学的だが組織文脈もある場合は **`needs_review`**。
- 組織文脈の語が 1 つだけ（弱い）場合も **`needs_review`**。

採用寄りにするには、組織・人・心理・世代・文化に関する語（employee, organization,
company, team, management, leadership, HRM, psychological safety, employee voice,
knowledge sharing, mentoring, resistance to change, technology adoption,
work values, generation, intergenerational, ageism, power distance など）が
**複数**あることを条件にしている。

判定理由は **`screening_reason`** 列に記録する
（例: `medical/public health context: ...` / `physical workplace environment, not organizational workplace: ...`）。

旧来の個別チェック（country_check / category_check / pdf_check）も内部で使う。
PDF が無いが条件を満たす候補は `manual_download_needed`。

## 重複チェック

1. DOI 一致
2. タイトルの正規化一致
3. タイトル類似度が高い場合（`rapidfuzz` による fuzzy 比較）

重複時は `duplicate_of` に原本 `id` を記録し、`screening_status` を `duplicate` にする。

## PDF 自動取得の注意

- ダウンロードは **OA PDF URL がある候補のみ**。出版社サイトのスクレイピングは行わない。
- 保存形式: `pdfs/{country}/{slot}_{short_title}.pdf`
- ファイル名にスペース・記号・日本語・長すぎるタイトルは使わない（ASCII スラグ化）。

## PDF 自動取得の安全ルール

`download_oa_pdfs.py` は **無料・合法・Open Access と確認できた PDF だけ**を自動保存する。
少しでも不明・有料・ログイン必須・非公式の可能性があるものは **保存せず**、メタデータと URL
だけを残して人間確認に回す。保存前に `legal_safety_check()` で次を確認する。

**自動ダウンロード OK の条件（すべて満たす）**

- Unpaywall または OpenAlex で Open Access と確認できる（`is_oa` または `oa_status` が gold/green/hybrid/bronze/diamond）
- `pdf_url` が明示されている
- ドメインが信頼できる公開元（出版社公式 OA・大学リポジトリ・政府機関・PMC/PubMed Central・arXiv・DOAJ・MDPI・Frontiers・SpringerOpen・BMC・PLOS など）
- HTTP ステータス 200 / `Content-Type` が `application/pdf`（または先頭が `%PDF`）
- ログインページ・HTML ページではない / ファイルサイズが極端に小さくない

**自動ダウンロードしない（保存しない）条件**

- Sci-Hub・LibGen など非公式・違法性の高いドメイン
- ResearchGate・Academia.edu など権利状態が不明な個人アップロード
- 401 / 403 / 429、HTML しか返らない、paywall・login・access denied・purchase・rent・subscribe を含むページ
- PDF 拡張子も `Content-Type` も PDF でない / Open Access か不明 / ライセンス不明

**遵守事項**: 403・401・429 を**回避しない**（リトライ・**User-Agent 偽装・アクセス制限回避をしない**）。
API のレート制限を守る。Unpaywall / OpenAlex / Crossref / Semantic Scholar には実メール
（`UNPAYWALL_EMAIL` / `OPENALEX_MAILTO`）または API キーを使う。

判定結果は candidates.csv の次の列に記録する: `legal_download_status`, `legal_download_reason`,
`oa_source`, `license`, `pdf_checked_at`。`legal_download_status` の値:

| 値 | 意味 |
|----|------|
| `safe_oa_downloaded` | OA と確認し保存した |
| `safe_oa_but_download_failed` | 安全だが通信/書込みエラーで保存できず |
| `manual_check_required` | OA/ドメイン不明など、人間確認が必要 |
| `paywalled_or_login_required` | 有料/ログイン必須（401 含む） |
| `blocked_403` | 403 Forbidden（回避しない） |
| `not_pdf_response` | PDF ではない応答（HTML 等） |
| `illegal_or_untrusted_source` | Sci-Hub 等の非公式・違法性の高い元 |
| `unknown_license` | Open Access と確認できない |

各実行で `reports/download_safety_log.csv` を出力する
（列: `country,category,title,doi,pdf_url,domain,oa_status,license,legal_download_status,legal_download_reason,local_pdf_path`）。

> **PDF バイナリは GitHub にコミットしない**。`.gitignore` で `*.pdf` / `pdfs/` /
> `organized_pdfs/` / `khcoder_ready/` を除外している。

## PDF のファイル名整理 (`scripts/organize_pdfs.py`)

download 済み PDF を、研究用の分類ルールに基づいた分かりやすいファイル名へ整理する。
**元の PDF は消さず、`organized_pdfs/` 配下に安全のためコピー**する。

入力は `data/candidates.csv`（無ければ `reports/candidate_table.csv`）。
列名が多少違っても落ちないよう存在確認しながら読む。

### カテゴリ番号ルール

| 番号 | カテゴリ |
|------|----------|
| 1 | Stereotypes / Ageism |
| 2 | Work Values / Work Ethic |
| 3 | Knowledge Transfer / Mentoring |
| 4 | Communication / Psychological Safety |
| 5 | Change Adaptation / Technology Adoption |
| 6 | Status Quo Bias / Resistance to Change |

### ファイル名ルール

```text
{country}_{category_number}-{sequence}_{short_title}.pdf
```

例:

```text
organized_pdfs/Vietnam/Vietnam_1-1_ageism_workplace.pdf
organized_pdfs/Vietnam/Vietnam_1-2_generational_stereotype.pdf
organized_pdfs/Vietnam/Vietnam_4-1_employee_voice_power_distance.pdf
```

- `sequence` は **同じ国・同じカテゴリ内で 1 から連番**。別カテゴリはそれぞれ 1 から。
- `short_title` は title から生成: 小文字化 / ASCII 化 / 英数字とアンダースコアのみ /
  空白・記号は `_` / 連続 `_` は 1 つ / 50 文字以内 / 先頭末尾の `_` は削除。
  - 例: `"Employee Voice and Power Distance in Vietnam"`
    → `employee_voice_and_power_distance_in_vietnam`

### 対象にする PDF

次をすべて満たすものだけを対象にする:

- `local_pdf_path` が空でない
- ファイルが実際に存在する
- `screening_status` が `rejected` / `duplicate` / `exclude_country_mismatch` ではない

`adopted` / `strong_candidate` を優先して若い連番を割り当てる。`candidate` も対象に含む。

### 安全ルール

1. 元の PDF は削除しない（コピー方式）
2. 既存ファイルを上書きしない
3. 同名ファイルがある場合は `_dup1`, `_dup2` … を付ける
4. `--dry-run` で実行前に確認できる
5. 実行ログを `reports/rename_log.csv` に出力する
   （列: `country,category,category_number,sequence,title,source_path,new_path,status,note`、
   status は `copied` / `dry_run` / `skipped_no_pdf` / `skipped_missing_file` /
   `skipped_rejected` / `duplicate_filename_adjusted` / `error`）

### 使い方

```bash
# dry-run（コピーせず、何をするかだけログ出力）
python scripts/organize_pdfs.py --country Vietnam --dry-run

# 実際にコピー整理
python scripts/organize_pdfs.py --country Vietnam

# 3か国すべてを整理
python scripts/organize_pdfs.py --all-countries
```

PowerShell でも同様:

```powershell
python .\scripts\organize_pdfs.py --country Vietnam --dry-run
python .\scripts\organize_pdfs.py --country Vietnam
python .\scripts\organize_pdfs.py --all-countries
```

> **元の PDF (`pdfs/...`) は削除されません。** `organized_pdfs/` にコピーが作られるだけです。
> まずは `--dry-run` で `reports/rename_log.csv` を確認してから本実行することを推奨します。

## KH Coder 用前処理 (`scripts/preprocess_pdfs_for_khcoder.py`)

`organized_pdfs/` に整理済みの PDF からテキストを抽出し、卒業研究の前処理ルールに基づいて
KH Coder に投入できる `khcoder_cleaned.txt` を生成する。
PDF 抽出は **pymupdf を優先**し、`pdfplumber` → `pypdf` にフォールバックする。

### 入出力

入力: `organized_pdfs/{country}/*.pdf`
出力: PDF ごとに次のフォルダを作る。

```text
khcoder_ready/
  Vietnam/
    Vietnam_4-1_employee_voice_power_distance/
      original.pdf          # 元PDFのコピー
      extracted_raw.txt     # 抽出した生テキスト
      khcoder_cleaned.txt   # 整形済み（KH Coder 投入用）
      cleaning_notes.txt    # 残した/削除したセクション・警告・語数
      processing_log.txt    # 処理ログ
```

まとめ: `reports/khcoder_preprocess_log.csv`
（列: `country,pdf_file,category_number,category_name,raw_text_path,cleaned_text_path,notes_path,status,word_count_raw,word_count_cleaned,warnings`、
status は `processed` / `skipped_no_text` / `ocr_required` / `error`）。

カテゴリ番号はファイル名から読み取る（例 `Vietnam_4-1_...` → country=Vietnam,
category_number=4, category_name=Communication / Psychological Safety）。

### 前処理ルール（要約）

**残す**: Abstract / Introduction / Literature Review / Theoretical Background /
Background / Findings / Results の質的説明 / Discussion / Conclusion / Summary /
Policy Recommendation / Limitations / インタビュー引用・参加者の語り・自由記述・ケース記述。

**Methods は最小限のみ残す**: 対象国・対象組織・対象者・研究方法の最小説明・
インタビュー/質的研究である旨。統計的な記述（Cronbach's α、p値、係数、t値、SEM/PLS-SEM の数値表）は削除。

**削除する**: References / Bibliography / Appendix / Acknowledgements / Funding /
Author Contributions / Conflict of Interest / Ethics / Data availability /
Informed consent / Copyright / Table of Contents / 著者所属 / メールアドレス /
DOI・URL だけの行 / ページ番号 / 統計表・回帰表 / **References 以降の全文**。

**例外的に残す**: References/Appendix 内でも、インタビュー引用や Gioia table の引用文
（first-order concept / second-order theme / aggregate dimension）は残す。

**テキスト整形**: UTF-8 plain text / 段落間に空行 / 不自然な改行とハイフネーションを修正 /
`-LRB-`→`(`・`-RRB-`→`)` / `⏎` 削除 / 連続スペースを1つに / 元論文の言語は維持。

### OCR について

初期実装では **OCR は必須にしない**。抽出テキストが極端に少ない PDF（語数が少ない／
スキャン画像のみ）は無理に処理せず、`cleaning_notes.txt` と `processing_log.txt`、
まとめ CSV に `OCR required`（必要なら `scanned PDF suspected`）と記録してスキップする。
これらは別途 OCR をかけてから再処理する。

### 使い方

```bash
# 国別
python scripts/preprocess_pdfs_for_khcoder.py --country Vietnam

# 全国家
python scripts/preprocess_pdfs_for_khcoder.py --all-countries

# 特定PDFだけ
python scripts/preprocess_pdfs_for_khcoder.py --input organized_pdfs/Vietnam/Vietnam_1-1_xxx.pdf
```

PowerShell:

```powershell
python .\scripts\preprocess_pdfs_for_khcoder.py --country Vietnam
python .\scripts\preprocess_pdfs_for_khcoder.py --all-countries
python .\scripts\preprocess_pdfs_for_khcoder.py --input organized_pdfs\Vietnam\Vietnam_1-1_xxx.pdf
```

> 前処理は自動の補助です。References/Appendix の除去や質的記述の保持はヒューリスティックに行うため、
> **最終的な `khcoder_cleaned.txt` の内容は必ず人間が確認**してから KH Coder に投入してください。

## 重要な方針

このシステムは論文の最終採用を完全自動化しない。最終的に人間が次を確認する:

- 対象国が正しいか
- 職場文脈があるか
- カテゴリに合っているか
- PDF や Abstract が使えるか
- 1本目と重複していないか
