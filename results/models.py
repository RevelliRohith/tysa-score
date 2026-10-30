from django.db import models

class PublicScoreboardSnapshot(models.Model):
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='scoreboard_snapshots')
    event_config = models.ForeignKey('championships.EventConfiguration', on_delete=models.CASCADE, related_name='scoreboard_snapshots')
    age_category = models.ForeignKey('championships.AgeCategory', on_delete=models.CASCADE, related_name='scoreboard_snapshots')
    gender = models.CharField(max_length=10, choices=[('M', 'Male'), ('F', 'Female')])
    round_type = models.CharField(max_length=20, default='PRELIMINARY')
    
    # Pre-calculated published JSON payload containing:
    # {
    #   "current_performer": { ... },
    #   "top_scores": [ ... ],
    #   "cumulative_released": [ ... ],
    #   "total_count": 45,
    #   "performed_count": 18
    # }
    data = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('championship', 'event_config', 'age_category', 'gender', 'round_type')

    def __str__(self):
        return f"Snapshot: {self.event_config.get_event_type_display()} - {self.age_category.name} ({self.gender})"
