#!/usr/bin/env python3
"""
RAG Chunker — VLTHR DEVOPS
Walks the entire DEVOPS directory, reads all text files,
chunks them into ~500-token segments with ~50-token overlap,
and stores chunks as JSONL files organized by folder.

Output: rag_chunks/
  ├── _index.json           (master index: folder -> file -> chunk count)
  ├── _stats.json           (global stats)
  ├── root.jsonl            (chunks for root-level files)
  ├── pipeline.jsonl        (chunks for pipeline/ folder)
  ├── pipeline_engine.jsonl (chunks for pipeline/engine/)
  ├── backend.jsonl         (chunks for backend/)
  ├── ...
"""

import os
import json
import time
import hashlib
from pathlib import Path

try:
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    def count_tokens(text):
        return len(enc.encode(text))
    def encode_tokens(text):
        return enc.encode(text)
except Exception:
    # Fallback: rough estimate
    def count_tokens(text):
        return len(text) // 4
    def encode_tokens(text):
        return text.split()

# ── Config ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
OUTPUT_DIR = ROOT / "rag_chunks"
CHUNK_SIZE = 500       # target tokens per chunk
CHUNK_OVERLAP = 50     # token overlap between chunks
MAX_FILE_SIZE = 2_000_000  # skip files larger than 2MB (text files only)
MAX_LINE_LENGTH = 2000     # truncate lines longer than this

# File extensions to process
TEXT_EXTENSIONS = {
    ".py", ".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx",
    ".md", ".json", ".yml", ".yaml", ".html", ".css", ".scss",
    ".sh", ".bash", ".sql", ".csv", ".tsv",
    ".env", ".conf", ".cfg", ".ini", ".toml",
    ".txt", ".log", ".lock",
    ".dockerfile", ".gitignore", ".dockerignore",
    ".svg",
}

# Files without extensions to include (by name)
TEXT_FILENAMES = {
    "Dockerfile", ".gitignore", ".dockerignore", ".env", ".env.example",
    "run_ingestion.sh", ".gitkeep", ".bybit_throttle.lock",
    "=.max_daily_override", "=0.3",
}

# Directories to skip entirely
SKIP_DIRS = {
    "__pycache__", ".pytest_cache", "node_modules", "dist",
    ".git", "rag_chunks", "logs",
}

# File patterns to skip (even if extension matches)
SKIP_PATTERNS = {
    "package-lock.json",  # 150KB of lock data, not useful
}

# ── Helpers ─────────────────────────────────────────────────────────────

def should_process(filepath: Path) -> bool:
    """Determine if a file should be processed."""
    name = filepath.name
    ext = filepath.suffix.lower()

    # Skip by name pattern
    for pat in SKIP_PATTERNS:
        if pat in name:
            return False

    # Skip by extension
    if ext in TEXT_EXTENSIONS:
        return True

    # Skip by filename
    if name in TEXT_FILENAMES:
        return True

    # Dockerfile variants
    if name.startswith("Dockerfile"):
        return True

    return False


def read_file_lines(filepath: Path) -> list[str]:
    """Read file and return list of lines. Handles encoding errors."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"  [SKIP] Cannot read {filepath}: {e}")
        return []

    # Truncate overly long lines
    result = []
    for line in lines:
        if len(line) > MAX_LINE_LENGTH:
            result.append(line[:MAX_LINE_LENGTH] + " ...[truncated]\n")
        else:
            result.append(line)
    return result


def chunk_file(filepath: Path, lines: list[str]) -> list[dict]:
    """Chunk a file's lines into ~CHUNK_SIZE token segments with overlap."""
    if not lines:
        return []

    # Build line-by-line token counts
    line_tokens = []
    for line in lines:
        toks = encode_tokens(line)
        line_tokens.append((len(toks), toks))

    chunks = []
    current_lines = []
    current_tokens = []
    current_count = 0
    line_start = 1

    for i, (ntok, toks) in enumerate(line_tokens):
        if current_count + ntok > CHUNK_SIZE and current_lines:
            # Flush current chunk
            chunk_text = "".join(current_lines)
            chunk = {
                "content": chunk_text,
                "line_start": line_start,
                "line_end": i,
                "token_count": current_count,
            }
            chunks.append(chunk)

            # Start new chunk with overlap
            overlap_tokens = current_tokens[-CHUNK_OVERLAP:] if len(current_tokens) > CHUNK_OVERLAP else current_tokens
            overlap_lines = current_lines[-10:] if len(current_lines) > 10 else current_lines
            current_lines = list(overlap_lines)
            current_tokens = list(overlap_tokens)
            current_count = sum(t[0] for t in [(len(encode_tokens(l)), []) for l in overlap_lines])
            line_start = i + 1 - len(overlap_lines)

        current_lines.append(lines[i])
        current_tokens.extend(toks)
        current_count += ntok

    # Flush remaining
    if current_lines:
        chunk_text = "".join(current_lines)
        chunks.append({
            "content": chunk_text,
            "line_start": line_start,
            "line_end": len(lines),
            "token_count": current_count,
        })

    return chunks


def get_folder_key(filepath: Path, root: Path) -> str:
    """Get a normalized folder key for organizing output files."""
    rel = filepath.relative_to(root)
    parts = rel.parts

    if len(parts) == 1:
        return "root"

    # Use first 2-3 path components as folder key
    if len(parts) == 2:
        return parts[0]
    elif len(parts) == 3:
        return f"{parts[0]}_{parts[1]}"
    else:
        return f"{parts[0]}_{parts[1]}_{parts[2]}"


def get_file_type(filepath: Path) -> str:
    """Get file type category."""
    ext = filepath.suffix.lower().lstrip(".")
    if ext:
        return ext
    name = filepath.name
    if name.startswith("Dockerfile"):
        return "dockerfile"
    if name in (".gitignore", ".dockerignore"):
        return "gitignore"
    if name in (".env", ".env.example"):
        return "env"
    return "unknown"


def chunk_id(filepath: Path, chunk_index: int) -> str:
    """Generate a unique chunk ID."""
    h = hashlib.md5(str(filepath).encode()).hexdigest()[:8]
    return f"chunk_{h}_{chunk_index:04d}"


# ── Main ────────────────────────────────────────────────────────────────

def main():
    start_time = time.time()

    # Create output directory
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Stats
    stats = {
        "total_files_scanned": 0,
        "total_files_processed": 0,
        "total_files_skipped": 0,
        "total_chunks": 0,
        "total_tokens": 0,
        "by_folder": {},
        "by_type": {},
        "skipped_files": [],
        "large_files_skipped": [],
    }

    # Collect chunks by folder
    folder_chunks: dict[str, list[dict]] = {}
    folder_files: dict[str, list[str]] = {}

    print(f"Scanning: {ROOT}")
    print(f"Output: {OUTPUT_DIR}")
    print(f"Chunk size: {CHUNK_SIZE} tokens, overlap: {CHUNK_OVERLAP}")
    print()

    # Walk directory tree
    all_files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # Skip directories
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for filename in filenames:
            filepath = Path(dirpath) / filename
            all_files.append(filepath)

    # Sort for deterministic output
    all_files.sort()

    print(f"Found {len(all_files)} files total")

    for filepath in all_files:
        stats["total_files_scanned"] += 1

        # Check if should process
        if not should_process(filepath):
            stats["total_files_skipped"] += 1
            continue

        # Check file size
        try:
            fsize = filepath.stat().st_size
        except Exception:
            continue

        if fsize > MAX_FILE_SIZE:
            stats["large_files_skipped"].append({
                "file": str(filepath.relative_to(ROOT)),
                "size_mb": round(fsize / 1_000_000, 2),
            })
            continue

        # Read file
        lines = read_file_lines(filepath)
        if not lines:
            stats["total_files_skipped"] += 1
            continue

        stats["total_files_processed"] += 1

        # Chunk the file
        chunks = chunk_file(filepath, lines)

        if not chunks:
            continue

        # Get metadata
        folder_key = get_folder_key(filepath, ROOT)
        ftype = get_file_type(filepath)
        rel_path = str(filepath.relative_to(ROOT))

        # Track stats
        if folder_key not in stats["by_folder"]:
            stats["by_folder"][folder_key] = {"files": 0, "chunks": 0, "tokens": 0}
        stats["by_folder"][folder_key]["files"] += 1
        stats["by_folder"][folder_key]["chunks"] += len(chunks)

        if ftype not in stats["by_type"]:
            stats["by_type"][ftype] = {"files": 0, "chunks": 0}
        stats["by_type"][ftype]["files"] += 1
        stats["by_type"][ftype]["chunks"] += len(chunks)

        # Build chunk objects
        for ci, chunk in enumerate(chunks):
            chunk_obj = {
                "id": chunk_id(filepath, ci),
                "file_path": rel_path,
                "folder": folder_key,
                "file_type": ftype,
                "file_size": fsize,
                "line_start": chunk["line_start"],
                "line_end": chunk["line_end"],
                "token_count": chunk["token_count"],
                "content": chunk["content"],
            }
            folder_chunks.setdefault(folder_key, []).append(chunk_obj)
            stats["total_chunks"] += 1
            stats["total_tokens"] += chunk["token_count"]

        folder_files.setdefault(folder_key, []).append(rel_path)

        if stats["total_files_processed"] % 20 == 0:
            print(f"  Processed {stats['total_files_processed']} files, {stats['total_chunks']} chunks so far...")

    # Write output files
    print()
    print("Writing chunk files...")

    index = {}
    for folder_key, chunks in sorted(folder_chunks.items()):
        outfile = OUTPUT_DIR / f"{folder_key}.jsonl"
        with open(outfile, "w", encoding="utf-8") as f:
            for chunk in chunks:
                f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

        index[folder_key] = {
            "chunk_file": f"{folder_key}.jsonl",
            "file_count": len(folder_files.get(folder_key, [])),
            "chunk_count": len(chunks),
            "files": sorted(folder_files.get(folder_key, [])),
        }
        print(f"  {folder_key}.jsonl: {len(chunks)} chunks from {len(folder_files.get(folder_key, []))} files")

    # Write master index
    with open(OUTPUT_DIR / "_index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    # Write stats
    elapsed = time.time() - start_time
    stats["elapsed_seconds"] = round(elapsed, 2)
    stats["output_size_mb"] = 0  # will calculate below

    with open(OUTPUT_DIR / "_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    # Calculate output size
    total_size = sum(f.stat().st_size for f in OUTPUT_DIR.iterdir())
    stats["output_size_mb"] = round(total_size / 1_000_000, 2)

    # Re-write stats with size
    with open(OUTPUT_DIR / "_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print()
    print("=" * 60)
    print(f"COMPLETE in {elapsed:.1f}s")
    print(f"Files scanned:    {stats['total_files_scanned']}")
    print(f"Files processed:  {stats['total_files_processed']}")
    print(f"Files skipped:    {stats['total_files_skipped']}")
    print(f"Large files skip: {len(stats['large_files_skipped'])}")
    print(f"Total chunks:     {stats['total_chunks']}")
    print(f"Total tokens:     {stats['total_tokens']:,}")
    print(f"Output size:      {stats['output_size_mb']} MB")
    print(f"Output dir:       {OUTPUT_DIR}")
    print(f"Folders indexed:  {len(index)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
