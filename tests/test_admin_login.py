import pytest
from django.contrib.auth import get_user_model
from audit.models import AuditLog


@pytest.mark.django_db
def test_admin_login_and_dashboard_with_system_audit_events(client):
    admin = get_user_model().objects.create_superuser(
        email='admin@tysa.org', password='admin123')
    AuditLog.log('ADMIN_PASSWORD_RESET', 'User', admin.pk,
                 {'reason': 'User-authorized login repair'})

    response = client.post('/accounts/login/', {
        'email': admin.email, 'password': 'admin123'})
    assert response.status_code == 302
    assert response['Location'] == '/dashboard/'
    for _ in range(2):
        response = client.get('/dashboard/')
        assert response.status_code == 200
        assert b'System' in response.content
        assert client.session['_auth_user_id'] == str(admin.pk)
