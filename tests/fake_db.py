"""
内存假数据库：替代 models.Database，记录所有执行的 SQL 以便断言。
用法：monkeypatch.setattr(models_mod, 'db', FakeDB())
"""


class FakeDB:
    def __init__(self):
        self.executed = []            # [(sql, params), ...] 按执行顺序
        self._auto_id = 0
        self.query_side_effect = None   # callable(sql, params) -> list[dict]
        self.one_side_effect = None     # callable(sql, params) -> dict | None

    def query(self, sql, params=None):
        if self.query_side_effect:
            return self.query_side_effect(sql, params)
        return []

    def query_one(self, sql, params=None):
        if self.one_side_effect:
            return self.one_side_effect(sql, params)
        return None

    def execute(self, sql, params=None):
        self._auto_id += 1
        self.executed.append((sql, params))
        return 1, self._auto_id

    # ---- 断言辅助 ----
    def find_executed(self, fragment):
        """返回所有 SQL 中包含 fragment 的执行记录 [(sql, params), ...]"""
        return [(s, p) for s, p in self.executed if fragment in s]

    def assert_executed(self, fragment, count=1):
        hits = self.find_executed(fragment)
        assert len(hits) == count, (
            f"期望执行 {count} 次包含 {fragment!r} 的 SQL，实际 {len(hits)} 次:\n"
            + "\n".join(s for s, _ in self.executed)
        )
        return hits
