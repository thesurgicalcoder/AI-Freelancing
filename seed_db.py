"""Seed demo data (dev only):  DJANGO_DEBUG=true python seed_db.py"""
import os
import sys
from datetime import timedelta

import django

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.utils import timezone  # noqa: E402

from core_api import services  # noqa: E402
from core_api.models import Project, ProjectApplication, User  # noqa: E402

PASSWORD = os.getenv('SEED_PASSWORD', 'Demo-pass-2026')


def main():
    client, _ = User.objects.get_or_create(username='demo_client', defaults={'role': 'Client', 'email': 'client@example.com'})
    client.set_password(PASSWORD); client.save()
    freelancers = []
    for name in ('demo_dev_a', 'demo_dev_b'):
        f, _ = User.objects.get_or_create(username=name, defaults={'role': 'Freelancer', 'email': f'{name}@example.com'})
        f.set_password(PASSWORD); f.save()
        freelancers.append(f)
    services.deposit(client, '5000')

    Project.objects.filter(client=client).delete()
    for title, desc, budget in [
        ('Inventory REST API', 'Build a REST API for warehouse inventory with a React admin UI.', '2000'),
        ('Churn prediction service', 'Train and serve an ML model that predicts customer churn.', '3000'),
    ]:
        p = Project.objects.create(client=client, title=title, description=desc, budget=budget,
                                   deadline=timezone.now() + timedelta(days=30))
        services.generate_milestones(p)
        ProjectApplication.objects.create(project=p, freelancer=freelancers[0])
    print(f'Seeded. Log in as demo_client / demo_dev_a / demo_dev_b with password {PASSWORD!r}.')


if __name__ == '__main__':
    main()
