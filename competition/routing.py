from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/stage/(?P<champ_code>[\w-]+)/(?P<stage_id>[\w-]+)/$', consumers.StageConsumer.as_asgi()),
    re_path(r'^ws/results/(?P<champ_code>[\w-]+)/(?P<event_id>[\w-]+)/$', consumers.LiveResultsConsumer.as_asgi()),
    re_path(r'^ws/results/(?P<champ_code>[\w-]+)/$', consumers.LiveResultsConsumer.as_asgi()),
]
