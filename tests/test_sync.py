from datetime import date

from app.services.sync import metric_number, report_day


def test_parse_report_day_from_ozon_dimension():
    assert report_day({"name": "2026-09-02 00:00:00+00:00"}) == date(2026, 9, 2)


def test_parse_metric_from_object():
    assert metric_number({"value": "12.5"}) == 12.5
