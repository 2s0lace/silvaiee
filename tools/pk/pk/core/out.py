from dataclasses import dataclass, field
import json
import re

from .errors import PkError


def compact(value):
    return re.sub(r'\s+', ' ', str(value)).strip()


@dataclass
class Result:
    summary: str
    data: list = field(default_factory=list)
    ok: bool = True
    code: str = 'OK'
    more: int | None = None

    def render(self, as_json=False):
        data = [compact(line) for line in self.data]
        if as_json:
            return json.dumps(dict(ok=self.ok, code=self.code, summary=compact(self.summary),
                                   data=data, more=self.more), ensure_ascii=False, separators=(',', ':'))
        status = ('OK ' if self.ok else f'ERR {self.code} ') + compact(self.summary)
        return '\n'.join([status, *data, *([] if self.more is None else [f'MORE {self.more}'])])


def page(summary, rows, offset=0, limit=20, maximum=20):
    if offset < 0 or not 1 <= limit <= maximum:
        raise PkError('BOUNDS', f'offset >=0 and limit 1..{maximum} required')
    rows = list(rows)
    end = offset + limit
    return Result(summary, rows[offset:end], more=end if end < len(rows) else None)
