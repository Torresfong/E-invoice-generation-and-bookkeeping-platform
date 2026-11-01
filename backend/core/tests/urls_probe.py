"""A test-only URL configuration: one view that reports what the database sees during a request."""

from django.db import connection
from django.http import JsonResponse
from django.urls import include, path

from core import jobs
from core.models import Company


def probe(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        role = cursor.fetchone()[0]
        cursor.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        bypasses_rls = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM pg_tables WHERE tableowner = current_user")
        tables_owned = cursor.fetchone()[0]
    return JsonResponse(
        {
            "role": role,
            "bypasses_rls": bypasses_rls,
            "tables_owned": tables_owned,
            "companies": sorted(Company.objects.values_list("name", flat=True)),
        }
    )


def boom(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 / 0")


def _run(sql, params):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()


def note_create(request):
    """Writes a record the way a business view will: as the signed-in user, on a table with the audit trigger."""
    row = _run(
        "INSERT INTO probe_note (company_id, body) VALUES (%s, %s) RETURNING id",
        [int(request.POST["company"]), request.POST["body"]],
    )
    return JsonResponse({"id": row[0]})


def note_update(request, pk):
    _run("UPDATE probe_note SET body = %s WHERE id = %s RETURNING id", [request.POST["body"], pk])
    return JsonResponse({"id": pk})


def note_delete(request, pk):
    _run("DELETE FROM probe_note WHERE id = %s RETURNING id", [pk])
    return JsonResponse({"id": pk})


def job_enqueue(request):
    """Queues a job the way a business view will: as the signed-in user, for a company named by the view."""
    jobs.enqueue("probe-job", company=Company(pk=int(request.POST["company"])), ref=request.POST.get("ref", "x"))
    return JsonResponse({"queued": True})


urlpatterns = [
    path("probe/", probe),
    path("jobs/", job_enqueue),
    path("boom/", boom),
    path("notes/", note_create),
    path("notes/<int:pk>/update/", note_update),
    path("notes/<int:pk>/delete/", note_delete),
    path("", include("config.urls")),
]
