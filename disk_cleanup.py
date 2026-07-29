# disk_cleanup.py
# 2026-07-21  Jonghyun Park w/ Claude
#
# 로컬 디스크 공간 확보 도구 (Windows 전용)
#   1) CACHE  : 재생성 가능한 캐시 폴더 내용물 삭제 (Temp / CrashDumps / npm-cache 등)
#   2) DEHYDRATE : OneDrive 파일을 "온라인 전용"으로 전환 (attrib -P +U)
#                  → 클라우드에는 그대로 남고 로컬 점유만 0 이 됨. 더블클릭하면 다시 받아짐.
#
# 기본은 DRY-RUN. 실제 적용은 --apply.
#   python disk_cleanup.py                 # 현황 리포트 + dry-run
#   python disk_cleanup.py --report        # 현황만 (어느 폴더가 로컬을 얼마나 먹는지)
#   python disk_cleanup.py --cache --apply
#   python disk_cleanup.py --dehydrate --apply

import argparse
import ctypes
import glob
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

# ════════════════════ 사용자가 바꿔야 하는 부분 ════════════════════

# ─── 실행 모드 (플래그 없이 --apply 만 줬을 때 무엇을 돌릴지) ───
RUN_CACHE_CLEAN = True          # 캐시 삭제 수행
RUN_DEHYDRATE = True            # OneDrive 온라인화 수행

# ─── OneDrive 온라인화 대상 ───
# 빈 문자열("") 이면 자동 탐지 — 환경변수 OneDriveCommercial / OneDrive / OneDriveConsumer
# → 없으면 홈 디렉터리의 "OneDrive*" 폴더. 계정이 여러 개거나 특정 하위 폴더만 다루려면 직접 지정
ONEDRIVE_ROOT = ""

# 온라인화할 확장자. 빈 리스트([]) 로 두면 확장자 무관 전체가 대상
DEHYDRATE_EXTS = [".png", ".jpg", ".jpeg", ".gif", ".mhtml", ".mp4", ".mov"]

# 이 폴더명이 경로에 들어가면 제외 (git 저장소·패키지 폴더를 온라인화하면 느려지고 깨짐)
DEHYDRATE_EXCLUDE_DIRS = [".git", "node_modules", "__pycache__", ".venv", "venv", ".ipynb_checkpoints"]

# 경로에 이 문자열이 들어가면 제외 (작업 중 폴더 지정용, 대소문자 무시)
DEHYDRATE_EXCLUDE_KEYWORDS = []          # 예: ["작업중", "in_progress"]

# 이 크기(MB) 미만 파일은 건너뜀 — 작은 파일은 온라인화 효과 대비 재다운로드 번거로움만 큼
DEHYDRATE_MIN_MB = 0.5

# ─── 캐시 삭제 대상 (폴더 자체는 유지하고 하위 항목만 삭제) ───
# %LOCALAPPDATA% 하위 폴더명으로 적는다. 절대경로를 적으면 그 경로를 그대로 사용
CACHE_TARGETS = [
    ("CrashDumps",          "앱 크래시 덤프"),
    (r"npm-cache\_cacache", "npm 패키지 캐시"),
    ("Temp",                "임시 파일"),
    # (r"pip\Cache",        "pip 캐시"),
    # ("ms-playwright",     "playwright 브라우저"),
]

# 캐시 폴더 안이라도 경로에 이 문자열이 있으면 삭제 제외 (대소문자 무시)
# ⚠ Temp\claude 는 실행 중인 Claude Code 세션의 작업 파일 — 지우면 진행 중 작업이 끊김
CACHE_PROTECT_KEYWORDS = [r"\claude"]

# 숫자를 넣으면 그보다 오래된 항목만 삭제 (None = 전부)
CACHE_MAX_AGE_DAYS = None

# ─── 공통 ───
DRIVE = "C:\\"                  # 여유 공간을 표시할 드라이브
PROGRESS_EVERY = 500            # N개 처리마다 진행률 출력
REPORT_TOP_N = 20               # --report 에서 보여줄 상위 폴더 수
LOG_TO_FILE = True              # 실행 로그를 스크립트 폴더에 남길지
LOG_BASENAME = "disk_cleanup_log"

# ════════════════════════ 내부 사용 ════════════════════════

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")

# ONEDRIVE_ROOT 가 비어 있을 때 순서대로 확인할 환경변수
ONEDRIVE_ENV_VARS = ["OneDriveCommercial", "OneDrive", "OneDriveConsumer"]


def resolve_onedrive_root():
    """ONEDRIVE_ROOT 자동 탐지 — 환경변수 → 홈의 OneDrive* 폴더. 못 찾으면 빈 문자열"""
    for var in ONEDRIVE_ENV_VARS:
        path = os.environ.get(var)
        if path and os.path.isdir(path):
            return path
    candidates = sorted(glob.glob(os.path.join(HOME, "OneDrive*")))
    for path in candidates:
        if os.path.isdir(path):
            return path
    return ""


def resolve_cache_target(name):
    """상대명이면 %LOCALAPPDATA% 하위로, 이미 절대경로면 그대로"""
    return name if os.path.isabs(name) else os.path.join(LOCALAPPDATA, name)


ONEDRIVE_ROOT = ONEDRIVE_ROOT or resolve_onedrive_root()

# Windows 파일 속성 비트 (OneDrive Files On-Demand 상태 판별용)
FILE_ATTRIBUTE_OFFLINE = 0x1000            # 온라인 전용 (내용 없음)
FILE_ATTRIBUTE_PINNED = 0x80000            # "이 장치에 항상 유지"
FILE_ATTRIBUTE_UNPINNED = 0x100000         # "공간 확보" 표시
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000  # 자리표시자(placeholder)
GET_FILE_ATTRIBUTES_FAILED = 0xFFFFFFFF

_log_lines = []


def log(msg=""):
    print(msg)
    _log_lines.append(str(msg))


def flush_log():
    if not LOG_TO_FILE or not _log_lines:
        return
    ts = datetime.now().strftime("%y%m%d_%H%M")
    path = os.path.join(SCRIPT_DIR, f"{LOG_BASENAME}_{ts}.txt")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(_log_lines))
    print(f"\n로그: {path}")


def free_gb():
    return shutil.disk_usage(DRIVE).free / (1024 ** 3)


def get_attrs(path):
    """실패 시 None"""
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    return None if attrs == GET_FILE_ATTRIBUTES_FAILED else attrs


def is_local(attrs):
    """로컬에 실제 내용이 내려와 있는가 (온라인 전용/자리표시자가 아닌가)"""
    if attrs is None:
        return False
    return not (attrs & FILE_ATTRIBUTE_OFFLINE) and not (attrs & FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)


def walk_files(root):
    """os.scandir 재귀 — 접근 불가 폴더는 건너뜀"""
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            yield e
                    except OSError:
                        continue
        except OSError:
            continue


# ──────────────────────── 1) 현황 리포트 ────────────────────────

def collect_hydrated(root):
    """로컬에 내려와 있는 파일 목록 [(path, size)]"""
    out = []
    for e in walk_files(root):
        attrs = get_attrs(e.path)
        if not is_local(attrs):
            continue
        try:
            out.append((e.path, e.stat(follow_symlinks=False).st_size))
        except OSError:
            continue
    return out


def report():
    log(f"[리포트] {ONEDRIVE_ROOT}")
    if not os.path.isdir(ONEDRIVE_ROOT):
        log("  ⚠ 경로 없음 — ONEDRIVE_ROOT 상수 확인")
        return
    files = collect_hydrated(ONEDRIVE_ROOT)
    total = sum(s for _, s in files)
    log(f"  로컬 점유 {total / 1024**3:,.1f} GB / {len(files):,} 파일")

    by_dir, by_ext = {}, {}
    for p, s in files:
        by_dir[os.path.dirname(p)] = by_dir.get(os.path.dirname(p), 0) + s
        by_ext[os.path.splitext(p)[1].lower()] = by_ext.get(os.path.splitext(p)[1].lower(), 0) + s

    log("\n  ── 로컬 점유 상위 폴더")
    for d, s in sorted(by_dir.items(), key=lambda kv: -kv[1])[:REPORT_TOP_N]:
        log(f"    {s / 1024**3:7.2f} GB  {d.replace(ONEDRIVE_ROOT, '~')}")

    log("\n  ── 로컬 점유 상위 확장자")
    for x, s in sorted(by_ext.items(), key=lambda kv: -kv[1])[:REPORT_TOP_N]:
        log(f"    {s / 1024**3:7.2f} GB  {x or '(확장자 없음)'}")


# ──────────────────────── 2) 캐시 삭제 ────────────────────────

def dir_size(path):
    total = 0
    for e in walk_files(path):
        try:
            total += e.stat(follow_symlinks=False).st_size
        except OSError:
            pass
    return total


def protected(path):
    low = path.lower()
    return any(k.lower() in low for k in CACHE_PROTECT_KEYWORDS)


def too_new(path):
    if CACHE_MAX_AGE_DAYS is None:
        return False
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return False
    return mtime > datetime.now() - timedelta(days=CACHE_MAX_AGE_DAYS)


def clean_cache(apply):
    log("\n[캐시 삭제]" + ("" if apply else "  (dry-run)"))
    freed = 0
    for target, desc in CACHE_TARGETS:
        if not os.path.isdir(target):
            log(f"  - {desc}: 경로 없음 ({target})")
            continue
        size = dir_size(target)
        log(f"  - {desc}: {size / 1024**3:,.2f} GB  {target}")
        if not apply:
            freed += size
            continue

        removed = 0
        for name in os.listdir(target):
            child = os.path.join(target, name)
            if protected(child) or too_new(child):
                log(f"      skip(보호): {name}")
                continue
            before = dir_size(child) if os.path.isdir(child) else os.path.getsize(child)
            try:
                if os.path.isdir(child):
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    os.remove(child)
            except OSError:
                pass
            if not os.path.exists(child):
                removed += before
        freed += removed
        log(f"      삭제 {removed / 1024**3:,.2f} GB (사용 중 파일은 자동 skip)")
    log(f"  합계 {'삭제' if apply else '삭제 예정'} {freed / 1024**3:,.2f} GB")
    return freed


# ──────────────────── 3) OneDrive 온라인화 ────────────────────

def dehydrate_targets():
    exts = [e.lower() for e in DEHYDRATE_EXTS]
    ex_dirs = [f"\\{d.lower()}\\" for d in DEHYDRATE_EXCLUDE_DIRS]
    kws = [k.lower() for k in DEHYDRATE_EXCLUDE_KEYWORDS]
    min_bytes = DEHYDRATE_MIN_MB * 1024 * 1024

    for e in walk_files(ONEDRIVE_ROOT):
        low = e.path.lower()
        if exts and os.path.splitext(low)[1] not in exts:
            continue
        if any(d in low for d in ex_dirs):
            continue
        if any(k in low for k in kws):
            continue
        attrs = get_attrs(e.path)
        if not is_local(attrs):
            continue
        try:
            size = e.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        if size < min_bytes:
            continue
        yield e.path, size


def dehydrate(apply):
    log("\n[OneDrive 온라인화]" + ("" if apply else "  (dry-run)"))
    if not os.path.isdir(ONEDRIVE_ROOT):
        log("  ⚠ 경로 없음 — ONEDRIVE_ROOT 상수 확인")
        return 0

    targets = list(dehydrate_targets())
    total = sum(s for _, s in targets)
    log(f"  대상 {len(targets):,} 파일 / {total / 1024**3:,.2f} GB"
        f"  (확장자: {', '.join(DEHYDRATE_EXTS) if DEHYDRATE_EXTS else '전체'}, {DEHYDRATE_MIN_MB}MB 이상)")
    if not apply:
        for p, s in sorted(targets, key=lambda kv: -kv[1])[:10]:
            log(f"    {s / 1024**2:8.1f} MB  {p.replace(ONEDRIVE_ROOT, '~')}")
        log("    ... (--apply 로 실제 전환)")
        return total

    ok = 0
    start_free = free_gb()
    for i, (path, _) in enumerate(targets, 1):
        # -P: "항상 유지" 핀 해제 / +U: 온라인 전용 표시 → OneDrive 가 로컬 내용 회수
        rc = subprocess.run(["attrib", "-P", "+U", path],
                            capture_output=True, shell=False).returncode
        if rc == 0:
            ok += 1
        if i % PROGRESS_EVERY == 0:
            log(f"    {i:,}/{len(targets):,} 처리, 여유 {free_gb():,.2f} GB")
    log(f"  완료 {ok:,}/{len(targets):,} — 확보 {free_gb() - start_free:,.2f} GB")
    return total


# ──────────────────────────── main ────────────────────────────

def main():
    if sys.platform != "win32":
        print("Windows 전용 스크립트입니다.")
        return

    ap = argparse.ArgumentParser(description="로컬 디스크 공간 확보 (캐시 삭제 / OneDrive 온라인화)")
    ap.add_argument("--apply", action="store_true", help="실제 수행 (기본은 dry-run)")
    ap.add_argument("--cache", action="store_true", help="캐시 삭제만")
    ap.add_argument("--dehydrate", action="store_true", help="OneDrive 온라인화만")
    ap.add_argument("--report", action="store_true", help="현황 리포트만 (변경 없음)")
    args = ap.parse_args()

    log(f"=== disk_cleanup  {datetime.now():%Y-%m-%d %H:%M}  ({'APPLY' if args.apply else 'DRY-RUN'})")
    log(f"시작 여유 공간: {free_gb():,.2f} GB\n")

    if args.report:
        report()
        flush_log()
        return

    do_cache = args.cache or (not args.dehydrate and RUN_CACHE_CLEAN)
    do_dehydrate = args.dehydrate or (not args.cache and RUN_DEHYDRATE)

    if do_cache:
        clean_cache(args.apply)
    if do_dehydrate:
        dehydrate(args.apply)

    log(f"\n종료 여유 공간: {free_gb():,.2f} GB")
    if not args.apply:
        log("※ dry-run 입니다. 실제 적용하려면 --apply 를 붙이세요.")
    flush_log()


if __name__ == "__main__":
    main()
