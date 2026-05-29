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

## 自動判定ルール

`rules/` 配下の YAML で調整できる:

- `rules/country_keywords.yml` … 対象国判定の語
- `rules/category_keywords.yml` … 6カテゴリのキーワード
- `rules/inclusion_rules.yml` … 職場文脈語・格上げ条件・年代方針

判定の流れ:

- **country_check** … title/abstract/source に対象国語があるか（著者所属だけでは判定しない）
- **workplace_check** … 職場・組織文脈の語があるか
- **category_check** … カテゴリキーワードの一致数・一致語を記録
- **pdf_check** … OA PDF があるか。無ければ `manual_download_needed`

## 重複チェック

1. DOI 一致
2. タイトルの正規化一致
3. タイトル類似度が高い場合（`rapidfuzz` による fuzzy 比較）

重複時は `duplicate_of` に原本 `id` を記録し、`screening_status` を `duplicate` にする。

## PDF 自動取得の注意

- ダウンロードは **OA PDF URL がある候補のみ**。出版社サイトのスクレイピングは行わない。
- 保存形式: `pdfs/{country}/{slot}_{short_title}.pdf`
- ファイル名にスペース・記号・日本語・長すぎるタイトルは使わない（ASCII スラグ化）。

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

## 重要な方針

このシステムは論文の最終採用を完全自動化しない。最終的に人間が次を確認する:

- 対象国が正しいか
- 職場文脈があるか
- カテゴリに合っているか
- PDF や Abstract が使えるか
- 1本目と重複していないか
