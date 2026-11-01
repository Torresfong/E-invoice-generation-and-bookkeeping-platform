"""A test-only URL configuration: one screen that uses the audited-read mechanism and one that skips it."""

from django.http import HttpResponse
from django.urls import path

from core import audit


def skipped(request, pk):
    return HttpResponse("company data")


@audit.audited_read("thing")
def covered(request, pk):
    return HttpResponse("company data")


urlpatterns = [
    path("skipped/<int:pk>/", skipped),
    path("covered/<int:pk>/", covered),
]
