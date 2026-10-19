from app.projects.service import _project_name_from_url


def test_project_name_from_url_strips_leading_www() -> None:
    assert _project_name_from_url("https://www.example.com/path") == "example.com"


def test_project_name_from_url_keeps_other_subdomains() -> None:
    assert _project_name_from_url("https://shop.example.com/path") == "shop.example.com"
