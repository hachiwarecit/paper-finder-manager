"""翻訳準備。

自動翻訳は必須にしない。まず翻訳対象テキストを抽出し、
固定プロンプト + 用語集と一緒に保存する。

保存物 (data/09_translated/<paper_id>/):
  - original_extracted.txt   抽出した原文
  - translation_prompt.txt   翻訳指示プロンプト (+ 用語集)
  - translated_en.txt        翻訳結果 (空で用意。人手 or API で埋める)
  - translation_log.json     処理ログ

ANTHROPIC_API_KEY があり anthropic がインストールされていれば、
translate=True で自動翻訳して translated_en.txt を埋めることもできる。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .extractor import analysis_text
from .models import PaperRecord
from .utils import DATA_DIR, get_logger, load_config, now_iso

logger = get_logger()

TRANSLATION_PROMPT = """Translate the following Thai academic text into English.

Rules:
1. Translate faithfully without summarizing.
2. Do not add explanations, interpretations, or examples.
3. Do not omit any sentences.
4. Preserve paragraph boundaries and section headings.
5. Use consistent English translations for repeated Thai terms.
6. Preserve Generation X, Generation Y, Generation Z, Baby Boomers, proper nouns, numerical values, and citations exactly.
7. Do not improve or rewrite the author's argument.
8. Output only the English translation.
"""


def _glossary_block() -> str:
    cfg = load_config("translation_glossary.yaml")
    lines = ["", "Glossary (use these exact English terms):"]
    for item in cfg.get("glossary", []):
        src = item.get("source", "")
        tgt = item.get("target", "")
        if src and tgt:
            lines.append(f"- {src} => {tgt}")
    preserve = cfg.get("preserve_verbatim", [])
    if preserve:
        lines.append("")
        lines.append("Preserve verbatim (do not translate): " + ", ".join(preserve))
    return "\n".join(lines)


def prepare_translation(rec: PaperRecord, raw_text: str, *, translate: bool = False) -> Path:
    """翻訳用ファイル一式を作成し、出力ディレクトリ Path を返す。"""
    out_dir = DATA_DIR / "09_translated" / rec.paper_id
    out_dir.mkdir(parents=True, exist_ok=True)

    extracted = analysis_text(raw_text) if raw_text else ""
    (out_dir / "original_extracted.txt").write_text(extracted, encoding="utf-8")

    prompt = TRANSLATION_PROMPT + "\n" + _glossary_block() + "\n"
    (out_dir / "translation_prompt.txt").write_text(prompt, encoding="utf-8")

    translated = ""
    method = "manual_pending"
    error = ""
    if translate:
        translated, method, error = _auto_translate(prompt, extracted)

    (out_dir / "translated_en.txt").write_text(translated, encoding="utf-8")

    log = {
        "paper_id": rec.paper_id,
        "original_language": rec.original_language,
        "char_count_extracted": len(extracted),
        "char_count_translated": len(translated),
        "method": method,
        "error": error,
        "timestamp": now_iso(),
    }
    (out_dir / "translation_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("翻訳準備完了: %s (method=%s)", out_dir, method)
    return out_dir


def _auto_translate(prompt: str, text: str) -> tuple[str, str, str]:
    """ANTHROPIC_API_KEY があれば翻訳する。失敗時は (空, method, error)。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "", "manual_pending", "ANTHROPIC_API_KEY 未設定のため自動翻訳をスキップ"
    if not text.strip():
        return "", "manual_pending", "翻訳対象テキストが空"
    try:
        import anthropic  # type: ignore

        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-fable-5",
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt + "\n\n---\n\n" + text}],
        )
        out = "".join(
            block.text for block in msg.content if getattr(block, "type", "") == "text"
        )
        return out, "anthropic_api", ""
    except ImportError:
        return "", "manual_pending", "anthropic パッケージ未インストール"
    except Exception as exc:  # noqa: BLE001
        return "", "manual_pending", f"翻訳API失敗: {exc}"
