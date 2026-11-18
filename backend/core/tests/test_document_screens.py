"""The document upload and list screens (ticket #14, D20, story 28).

The upload and local-check rules themselves are tested in test_documents.py and test_quality.py; these tests show
the screen applies them over HTTP, hides the upload form from a role that may not use it, refuses another
company's user, and reads in Chinese.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core import documents

from .factories import PASSWORD
from .test_quality import blank_page, sharp_page

CHINESE = {"accept-language": "zh-hans"}


def sign_in(client, user):
    assert client.post("/login/", {"email": user.email, "password": PASSWORD}).status_code == 302


def documents_url(company, *rest):
    return "/".join([f"/companies/{company.pk}/documents", *map(str, rest), ""])


def upload_file(data=None, name="invoice.png", content_type="image/png"):
    return {"file": SimpleUploadedFile(name, data or sharp_page(), content_type=content_type)}


@pytest.mark.parametrize("who", ["acme_owner", "acme_staff"])
def test_the_owner_and_staff_upload_and_see_it_listed(client, world, as_production_connection, who):
    with as_production_connection():
        sign_in(client, world[who])
        uploaded = client.post(documents_url(world["acme"]), upload_file())
        page = client.get(uploaded["Location"]).content.decode()

    assert uploaded.status_code == 302 and uploaded["Location"].startswith(documents_url(world["acme"]))
    assert "invoice.png" in page and "Uploaded." in page


def test_a_blurry_upload_is_listed_with_its_bounce_reason(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        uploaded = client.post(documents_url(world["acme"]), upload_file(data=blank_page()))
        page = client.get(uploaded["Location"]).content.decode()

    assert "too blurry" in page


def test_an_accountant_sees_the_list_without_a_form_and_cannot_post(client, world, as_production_connection):
    documents.upload_document(
        world["acme_owner"], world["acme"], filename="invoice.png", content_type="image/png", data=sharp_page()
    )
    with as_production_connection():
        sign_in(client, world["accountant"])
        page = client.get(documents_url(world["acme"])).content.decode()
        refused = client.post(documents_url(world["acme"]), upload_file(name="planted.png"))

    assert "invoice.png" in page and 'name="file"' not in page
    assert refused.status_code == 403
    assert "planted.png" not in page


def test_a_users_own_company_only(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["beta_owner"])
        refused_list = client.get(documents_url(world["acme"]))
        refused_upload = client.post(documents_url(world["acme"]), upload_file())

    assert refused_list.status_code == 404 and refused_upload.status_code == 404


def test_the_bounce_reason_reads_in_chinese(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        uploaded = client.post(documents_url(world["acme"]), upload_file(data=blank_page()))
        page = client.get(uploaded["Location"], headers=CHINESE).content.decode()

    assert "too blurry" not in page and "太模糊" in page
