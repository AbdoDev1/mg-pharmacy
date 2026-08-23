"""Focused authenticated baseline workloads for the local Docker benchmark.

Run one tagged workload at a time, for example:
    locust -f locustfile.py --headless --host=http://127.0.0.1:8081 \
      --tags inventory -u 1 -r 1 -t 30s --csv=/tmp/inventory

Credentials default to the disposable benchmark fixture and can be overridden
with BENCHMARK_STAFF_USERNAME/PASSWORD and BENCHMARK_CLIENT_USERNAME/PASSWORD.
"""

import os
import re

from locust import HttpUser, between, tag, task


CSRF_TOKEN_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')


def get_csrf_token(response):
    """Extract Django's masked CSRF value from an HTML form response."""
    match = CSRF_TOKEN_RE.search(response.text)
    if not match:
        raise ValueError("csrfmiddlewaretoken was not found in the form response")
    return match.group(1)


class _AuthenticatedUser(HttpUser):
    """Shared real HTTP form-login flow for the benchmark users."""

    abstract = True
    wait_time = between(0.2, 0.2)

    def _login(self, path, username, password, request_name):
        page = self.client.get(path, name=f"{request_name} login GET")
        csrf_token = get_csrf_token(page)
        response = self.client.post(
            path,
            {
                "username": username,
                "password": password,
                "csrfmiddlewaretoken": csrf_token,
            },
            headers={"Referer": self.client.base_url + path},
            allow_redirects=False,
            name=f"{request_name} login POST",
        )
        if response.status_code not in (301, 302, 303, 307, 308):
            response.failure(f"login returned HTTP {response.status_code}")


class StaffBenchmarkUser(_AuthenticatedUser):
    """Authenticated staff requests for inventory and all-time report baselines."""

    weight = 1

    def on_start(self):
        self._login(
            "/staff/login/",
            os.getenv("BENCHMARK_STAFF_USERNAME", "benchmark_staff"),
            os.getenv("BENCHMARK_STAFF_PASSWORD", "BenchmarkPass!2026"),
            "staff",
        )

    @tag("inventory")
    @task
    def inventory_movements(self):
        self.client.get("/staff/inventory/?tab=movements", name="/staff/inventory/?tab=movements")

    @tag("report")
    @task
    def products_sold_all_time(self):
        self.client.get(
            "/staff/reports/products/?period=all",
            name="/staff/reports/products/?period=all",
        )


class ClientStoreBenchmarkUser(_AuthenticatedUser):
    """Authenticated client requests for the normal store catalog page."""

    weight = 1

    def on_start(self):
        self._login(
            "/accounts/login/",
            os.getenv("BENCHMARK_CLIENT_USERNAME", "benchmark_client_0001"),
            os.getenv("BENCHMARK_CLIENT_PASSWORD", "BenchmarkPass!2026"),
            "client",
        )

    @tag("store")
    @task
    def authenticated_store(self):
        self.client.get("/store/", name="/store/ authenticated")
