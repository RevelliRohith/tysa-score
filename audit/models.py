from django.db import models
from django.conf import settings

class AuditLog(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=100, db_index=True)
    resource_type = models.CharField(max_length=100)
    resource_id = models.CharField(max_length=100, blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        user_str = self.user.email if self.user else "System"
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {user_str} - {self.action} on {self.resource_type}:{self.resource_id}"

    @classmethod
    def log(cls, action, resource_type, resource_id="", details=None, user=None, ip_address=None):
        return cls.objects.create(
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            details=details or {},
            user=user,
            ip_address=ip_address
        )
