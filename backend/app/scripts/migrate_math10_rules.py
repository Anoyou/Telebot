"""把旧“算数题”交互规则（action=math10）迁移成通用玩法规则（action="module"），可回滚。

说明见 ``app/services/interaction_rule_migration.py``。**默认只预演，不改数据。**

用法（容器内，仓库 backend 目录）::

    python -m app.scripts.migrate_math10_rules            # 预演：列出会迁移的规则
    python -m app.scripts.migrate_math10_rules --apply    # 执行迁移（原规则备份到 SystemSetting）
    python -m app.scripts.migrate_math10_rules --rollback --apply   # 回滚（只恢复迁移后未被编辑的规则）

迁移后交互 Bot 最多 5 秒内读到新配置（运行配置缓存 TTL）。
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.services.interaction_rule_migration import run_migration


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="迁移 / 回滚旧 action=math10 交互规则")
    parser.add_argument("--apply", action="store_true", help="真正写入（默认只预演）")
    parser.add_argument("--rollback", action="store_true", help="回滚之前的迁移")
    args = parser.parse_args(argv)
    report = asyncio.run(run_migration(dry_run=not args.apply, rollback=args.rollback))
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    if not args.apply:
        print("（预演模式，未写入。确认后加 --apply 执行。）")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
