"""
Load testing script for Radio Platform API using locust.

Install: pip install locust
Run:     locust -f tests/load_test.py --host https://studio-kolbramah-api-production.up.railway.app

Open http://localhost:8089 in your browser to configure and start the test.
"""
from locust import HttpUser, task, between


class PublicUser(HttpUser):
    """Simulates a public listener browsing stations."""

    wait_time = between(2, 5)
    weight = 8  # 80% of simulated users

    @task(3)
    def list_stations(self):
        self.client.get("/api/v1/stations")

    @task(5)
    def now_playing(self):
        # Hit now-playing for a station (will 404 if no station, that's fine for load testing)
        self.client.get("/api/v1/now-playing/test-station")

    @task(1)
    def health_check(self):
        self.client.get("/api/v1/health")


class AdminUser(HttpUser):
    """Simulates an admin user managing the platform."""

    wait_time = between(3, 8)
    weight = 2  # 20% of simulated users

    def on_start(self):
        """Login on start."""
        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin", "password": "613Radio"},
        )
        if response.status_code == 200:
            self.token = response.json().get("access_token", "")
            self.headers = {"Authorization": f"Bearer {self.token}"}
        else:
            self.token = ""
            self.headers = {}

    @task(3)
    def list_stations(self):
        self.client.get("/api/v1/stations", headers=self.headers)

    @task(2)
    def list_assets(self):
        self.client.get("/api/v1/assets", headers=self.headers)

    @task(2)
    def list_schedules(self):
        self.client.get("/api/v1/schedules/", headers=self.headers)

    @task(1)
    def list_holidays(self):
        self.client.get("/api/v1/holidays", headers=self.headers)

    @task(1)
    def list_sponsors(self):
        self.client.get("/api/v1/sponsors", headers=self.headers)

    @task(1)
    def analytics_summary(self):
        self.client.get("/api/v1/analytics/summary", headers=self.headers)

    @task(1)
    def analytics_top_assets(self):
        self.client.get("/api/v1/analytics/top-assets?limit=10", headers=self.headers)
