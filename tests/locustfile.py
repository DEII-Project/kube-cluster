from locust import HttpUser, task, between
import json

with open("./sample-body.json", "r") as file:
    sample_data = json.load(file)


class WebsiteUser(HttpUser):
    # wait_time = between(3, 5)

    @task
    def post_data(self):
        self.client.post("/predictions", json=sample_data)
