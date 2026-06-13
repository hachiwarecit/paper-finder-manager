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
| 検索 | `search` | OpenAlex から候補を取得（candidate として保存） |

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
