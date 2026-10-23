from datetime import date, datetime

import pytest

from agent.lead import parsers as p

REF = date(2026, 10, 23)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("150000", 150000),
        ("1,50,000", 150000),
        ("1.5 lakh", 150000),
        ("one and a half lakh", 150000),
        ("two lakh fifty thousand", 250000),
        ("around 3 lakhs", 300000),
        ("50k", 50000),
        ("eighty thousand rupees", 80000),
        ("rupees 2 lakh", 200000),
        ("1 crore", 10_000_000),
        ("five lakhs total", 500000),
        ("₹75,000", 75000),
        ("two and a half lakh", 250000),
    ],
)
def test_amount(text, expected):
    assert p.parse_amount_inr(text)[0] == expected


def test_amount_per_person_flag():
    assert p.parse_amount_inr("fifty thousand per person") == (50000, True)
    assert p.parse_amount_inr("1 lakh total") == (100000, False)


@pytest.mark.parametrize("text", ["50", "lots", "", "a few"])
def test_amount_rejects(text):
    with pytest.raises(ValueError):
        p.parse_amount_inr(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("+91 98765 43210", "+919876543210"),
        ("098765 43210", "+919876543210"),
        ("9876543210", "+919876543210"),
        ("91-9876543210", "+919876543210"),
        ("nine eight seven six five four three two one zero", "+919876543210"),
        ("nine eight double seven six five four three two one", "+919877654321"),
        ("+1 415 555 0100", "+14155550100"),
    ],
)
def test_phone(text, expected):
    assert p.normalize_phone(text) == expected


@pytest.mark.parametrize("text", ["12345", "98765", "hello"])
def test_phone_rejects(text):
    with pytest.raises(ValueError):
        p.normalize_phone(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("john dot smith at gmail dot com", "john.smith@gmail.com"),
        ("priya_r@Gmail.com", "priya_r@gmail.com"),
        ("a n a n d at outlook dot com", "anand@outlook.com"),
        ("rahul underscore 99 at yahoo dot co dot in", "rahul_99@yahoo.co.in"),
    ],
)
def test_email(text, expected):
    assert p.parse_email(text) == expected


def test_email_rejects():
    with pytest.raises(ValueError):
        p.parse_email("not an email")


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("12th to 19th December", date(2026, 12, 12), date(2026, 12, 19)),
        ("twelfth of December to the nineteenth", date(2026, 12, 12), date(2026, 12, 19)),
        ("December 12 to 19", date(2026, 12, 12), date(2026, 12, 19)),
        ("12 dec - 19 dec", date(2026, 12, 12), date(2026, 12, 19)),
        ("from 28 December to 3 January", date(2026, 12, 28), date(2027, 1, 3)),
        ("12/12/2026 to 19/12/2026", date(2026, 12, 12), date(2026, 12, 19)),
        ("5th January", date(2027, 1, 5), None),
        ("twenty first of November", date(2026, 11, 21), None),
    ],
)
def test_dates(text, start, end):
    assert p.parse_date_range(text, REF) == (start, end)


def test_date_rejects():
    with pytest.raises(ValueError):
        p.parse_date_range("sometime soon", REF)


@pytest.mark.parametrize(
    "text,expected",
    [("December", "2026-12"), ("next march", "2027-03"), ("in January 2028", "2028-01"), ("2026-11", "2026-11"), ("october", "2026-10")],
)
def test_month(text, expected):
    assert p.parse_month(text, REF) == expected


def test_when():
    now = datetime(2026, 10, 23, 9, 0, tzinfo=p.IST)  # Friday
    assert p.parse_when("tomorrow evening", now) == datetime(2026, 10, 24, 18, 0, tzinfo=p.IST)
    assert p.parse_when("today at 5 pm", now) == datetime(2026, 10, 23, 17, 0, tzinfo=p.IST)
    assert p.parse_when("monday morning", now) == datetime(2026, 10, 26, 10, 0, tzinfo=p.IST)
    assert p.parse_when("day after tomorrow at 11:30 am", now) == datetime(2026, 10, 25, 11, 30, tzinfo=p.IST)
    assert p.parse_when("whenever", now) is None


@pytest.mark.parametrize(
    "fn,text,expected",
    [
        (p.parse_trip_type, "it's our honeymoon", "honeymoon"),
        (p.parse_trip_type, "me and my wife", "couple"),
        (p.parse_trip_type, "with kids and grandparents", "parents"),
        (p.parse_trip_type, "a group of friends", "friends"),
        (p.parse_trip_type, "family", "family"),
        (p.parse_food, "we are pure vegetarian", "veg"),
        (p.parse_food, "non vegetarian", "non_veg"),
        (p.parse_food, "jain food", "jain"),
        (p.parse_food, "anything is fine", "no_preference"),
        (p.parse_flexibility, "a few days either way", "plus_minus_3"),
        (p.parse_flexibility, "dates are fixed", "fixed"),
        (p.parse_channel, "send it on whatsapp", "whatsapp"),
    ],
)
def test_enums(fn, text, expected):
    assert fn(text) == expected


def test_enum_rejects():
    with pytest.raises(ValueError):
        p.parse_trip_type("blue")


@pytest.mark.parametrize("text,expected", [("two", 2), ("2", 2), ("twenty three", 23), ("a couple", 2), ("just me", 1)])
def test_words_to_int(text, expected):
    assert p.words_to_int(text) == expected


def test_count_bounds():
    assert p.parse_count("we are 4 adults", minimum=1) == 4
    with pytest.raises(ValueError):
        p.parse_count("0", minimum=1, label="adults")


def test_names_and_cities():
    assert p.parse_name("my name is priya sharma") == "Priya Sharma"
    assert p.parse_city("from pune") == "Pune"
    assert p.parse_city("we are flying from navi mumbai") == "Navi Mumbai"


@pytest.mark.parametrize("text,expected", [("one child", 1), ("two kids", 2), ("three adults", 3), ("two infants", 2), ("one baby", 1)])
def test_counts_with_nouns(text, expected):
    assert p.parse_count(text, minimum=0, label="x") == expected
