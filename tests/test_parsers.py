from datetime import date
from decimal import Decimal

from pas.cbr import parse_currencies, parse_dynamic
from pas.fedstat import (IndicatorMeta, build_download_form, parse_indicator_page, parse_sdmx, parse_week,
                         plan_chunks)

CBR_DYNAMIC = (
    '<?xml version="1.0" encoding="windows-1251"?>'
    '<ValCurs ID="R01235" DateRange1="01.08.2026" DateRange2="02.08.2026" name="Foreign Currency Market Dynamic">'
    '<Record Date="01.08.2026" Id="R01235"><Nominal>1</Nominal><Value>79,4637</Value>'
    "<VunitRate>79,4637</VunitRate></Record></ValCurs>"
).encode("cp1251")

CBR_VALFULL = (
    '<?xml version="1.0" encoding="windows-1251"?><Valuta name="Foreign Currency Market Lib">'
    '<Item ID="R01235"><Name>Доллар США</Name><EngName>US Dollar</EngName><Nominal>1</Nominal>'
    "<ParentCode>R01235    </ParentCode><ISO_Num_Code>840</ISO_Num_Code><ISO_Char_Code>USD</ISO_Char_Code>"
    "</Item></Valuta>"
).encode("cp1251")

PAGE = r"""
<div class="dropdown" id="downloadTokenHolder">
  <input type="hidden" name="struts.token.name" value="token" />
<input type="hidden" name="token" value="TOK123" />
</div>
filters: {
    0: { title: 'Показатель', all: true,
         values: { 37426: { title: 'Indicator', order: 0, checked: true } }, indicator: true },
    3: { title: 'Year', all: false,
         values: { 2025: { title: '2025', order: 0, checked: false },
                   2026: { title: '2026', order: 1, checked: true } } },
    58273: { title: 'Products', all: false,
             values: { 1743470: { title: 'Картофель, кг', order: 0, checked: false } } }
},
left_columns: [ 58273 ], top_columns: [ 3 ], groups: [ ], filterObjectIds: [ ]
"""

SDMX = """<?xml version="1.0" encoding="utf-8"?>
<GenericData xmlns="http://www.SDMX.org/resources/SDMXML/schemas/v1_0/message"
             xmlns:generic="http://www.SDMX.org/resources/SDMXML/schemas/v1_0/generic"
             xmlns:structure="http://www.SDMX.org/resources/SDMXML/schemas/v1_0/structure">
  <CodeLists><structure:CodeList id="s_grtov"><structure:Name>t</structure:Name>
    <structure:Code value="2501"><structure:Description>Картофель, кг</structure:Description></structure:Code>
  </structure:CodeList></CodeLists>
  <DataSet><generic:Series>
    <generic:SeriesKey><generic:Value concept="s_OKATO" value="45000000000"/>
      <generic:Value concept="s_grtov" value="2501"/></generic:SeriesKey>
    <generic:Attributes><generic:Value concept="EI" value="рубль"/>
      <generic:Value concept="PERIOD" value="3 неделя 2025"/></generic:Attributes>
    <generic:Obs><generic:Time>2025</generic:Time><generic:ObsValue value="61,5"/></generic:Obs>
  </generic:Series></DataSet>
</GenericData>""".encode("utf-8")

CONCEPTS = {"region": "s_OKATO", "product": "s_grtov", "period": "PERIOD", "unit": "EI"}


def test_parse_cbr_dynamic():
    assert parse_dynamic(CBR_DYNAMIC) == [("R01235", date(2026, 8, 1), 1, Decimal("79.4637"), Decimal("79.4637"))]


def test_parse_cbr_currencies():
    assert parse_currencies(CBR_VALFULL) == [("R01235", "Доллар США", "US Dollar", 1, "R01235", "840", "USD")]


def test_parse_indicator_page_and_form():
    meta = parse_indicator_page(PAGE)
    assert meta.token == "TOK123"
    assert meta.filters[0]["title"] == "Показатель"
    assert meta.filters[3]["values"] == {2025: "2025", 2026: "2026"}
    assert (meta.left, meta.top) == ([58273], [3])

    form = build_download_form(meta, 37426, "T", {3: [2026]})
    assert ("lineObjectIds", "0") in form and ("lineObjectIds", "58273") in form
    assert ("columnObjectIds", "3") in form
    assert ("selectedFilterIds", "3_2026") in form and ("selectedFilterIds", "3_2025") not in form
    assert ("selectedFilterIds", "58273_1743470") in form


def test_parse_sdmx():
    rows, codes = parse_sdmx(SDMX, CONCEPTS)
    assert rows == [("45000000000", "2501", 2025, "3 неделя 2025", 3, Decimal("61.5"), "61,5", "рубль")]
    assert codes == [("s_grtov", "2501", "Картофель, кг")]


class FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *args, **kwargs):
        return self.rows


def test_plan_chunks_refreshes_latest_year_and_backfills_gaps():
    meta = IndicatorMeta(filters={58273: {"title": "p", "values": {1: "A", 2: "B"}},
                                  3: {"title": "y", "values": {2024: "2024", 2025: "2025", 2026: "2026"}}},
                         left=[], top=[], token="t")
    c = {"dimensions": {"product": 58273, "year": 3}, "start_year": 2024,
         "products_per_request": 1, "refresh_previous_year_days": 0}
    done = FakeConn([(2024, "A"), (2024, "B"), (2025, "A"), (2026, "A"), (2026, "B")])
    chunks = plan_chunks(done, meta, c, {58273: [1, 2]}, full=False, from_year=None)
    assert chunks == [(2026, [1], "refresh"), (2026, [2], "refresh"), (2025, [2], "backfill")]


def test_parse_week():
    assert parse_week("12 неделя 2026") == 12
    assert parse_week("01 неделя ( на 10 января)") == 1
    assert parse_week("на 1 декабря") is None
