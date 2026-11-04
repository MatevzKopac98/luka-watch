"""Read the contract tables you export by hand from Basketball-Reference ("Share & Export" > "Get as Excel").

  players: https://www.basketball-reference.com/contracts/players.html  -> one row per player and team
  teams:   https://www.basketball-reference.com/contracts/              -> payroll per team

The exports are HTML tables saved with an .xls name. Nothing here downloads from Basketball-Reference:
the files are exported in your own browser and dropped into data/contracts/.
"""
import re
from html.parser import HTMLParser


class _Table(HTMLParser):
    """Rows of {data-stat: text} (plus the player's id from its link) from the table body."""

    def __init__(self):
        super().__init__()
        self.rows, self.row, self.stat, self.text, self.in_body = [], None, None, [], False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tbody":
            self.in_body = True
        elif tag == "tr" and self.in_body:
            self.row = {} if "thead" not in (a.get("class") or "") else None
        elif tag in ("td", "th") and self.row is not None:
            self.stat, self.text = a.get("data-stat"), []
            if a.get("data-append-csv"):
                self.row["bbref_id"] = a["data-append-csv"]
        elif tag == "a" and self.row is not None and self.stat == "player":
            m = re.search(r"/players/\w/(\w+)\.html", a.get("href") or "")
            if m:
                self.row["bbref_id"] = m.group(1)

    def handle_data(self, data):
        if self.stat:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.row is not None and self.stat:
            self.row[self.stat] = "".join(self.text).strip()
            self.stat = None
        elif tag == "tr" and self.row is not None:
            if any(v for k, v in self.row.items() if k not in ("ranker", "bbref_id")):
                self.rows.append(self.row)
            self.row = None
        elif tag == "tbody":
            self.in_body = False


def _money(v):
    v = re.sub(r"[^\d]", "", v or "")
    return int(v) if v else None


def _season_headers(html):
    """{'y1': '2026-27', ...} from the table header."""
    return dict(re.findall(r'data-stat="(y\d)"[^>]*>(\d{4}-\d{2})<', html))


def read(path):
    html = open(path, encoding="utf-8", errors="replace").read()
    t = _Table()
    t.feed(html)
    return html, t.rows


def players(path):
    """[{name, bbref_id, team, salaries: {'2026-27': 12345678, ...}, guaranteed, signed_using}]."""
    html, rows = read(path)
    seasons = _season_headers(html)
    out = []
    for r in rows:
        if not r.get("player") or r.get("player") == "Player":
            continue
        sal = {seasons[k]: _money(r.get(k)) for k in seasons if _money(r.get(k))}
        out.append({"name": r["player"], "bbref_id": r.get("bbref_id"), "team": r.get("team_id"),
                    "salaries": sal, "guaranteed": _money(r.get("remain_gtd")),
                    "signed_using": r.get("signed_using") or None})
    return out, list(seasons.values())


def teams(path):
    """[{team, salaries: {'2026-27': payroll, ...}}]."""
    html, rows = read(path)
    seasons = _season_headers(html)
    return [{"team": r["team_name"], "salaries": {seasons[k]: _money(r.get(k)) for k in seasons if _money(r.get(k))}}
            for r in rows if r.get("team_name") and r.get("team_name") != "Team"], list(seasons.values())
