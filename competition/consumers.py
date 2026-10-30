import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser

class StageConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for private realtime stage coordination between Stage Manager
    and scoring desks for a specific Championship and Stage.
    
    Authorization Requirements:
    - User must be authenticated (anonymous users rejected with code 4401).
    - User must have active ScopedRole ('STAGE_MANAGER', 'CD', 'ADMIN', or assigned judge)
      for the specific championship and stage.
    """
    async def connect(self):
        self.champ_code = self.scope['url_route']['kwargs'].get('champ_code')
        self.stage_id = self.scope['url_route']['kwargs'].get('stage_id')
        self.user = self.scope.get('user')

        # Check authorization
        is_authorized = await self._is_authorized_for_stage()
        if not is_authorized:
            await self.close(code=4401)
            return

        self.room_group_name = f"stage_{self.champ_code}_{self.stage_id}"
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def stage_update(self, event):
        # Transmit private operational state to authorized stage client
        await self.send(text_data=json.dumps(event['data']))

    @database_sync_to_async
    def _is_authorized_for_stage(self):
        if not self.user or isinstance(self.user, AnonymousUser) or not self.user.is_authenticated:
            return False
        if self.user.is_superuser:
            return True
        # Check scoped role for this championship
        return self.user.scoped_roles.filter(
            championship__code=self.champ_code,
            role__in=['STAGE_MANAGER', 'CD', 'ADMIN']
        ).exists()


class LiveResultsConsumer(AsyncWebsocketConsumer):
    """
    Public WebSocket consumer for live scoreboards and audience displays.
    
    Scoped strictly by Championship and Event (e.g. `champ_code` and `event_id`).
    Broadcasts ONLY published snapshots (CD finalized or provisional released).
    Unpublished, in-progress, or draft revisions are strictly excluded.
    """
    async def connect(self):
        self.champ_code = self.scope['url_route']['kwargs'].get('champ_code')
        self.event_id = self.scope['url_route']['kwargs'].get('event_id', 'all')
        
        # Verify valid championship
        champ_exists = await self._championship_exists()
        if not champ_exists:
            await self.close(code=4404)
            return

        self.room_group_name = f"results_{self.champ_code}_{self.event_id}"
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

        # Send initial verified snapshot immediately upon connection/reconnection
        snapshot = await self._get_released_snapshot()
        await self.send(text_data=json.dumps({
            'type': 'snapshot',
            'data': snapshot
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def results_update(self, event):
        # Only forward sanitized public event data
        public_data = event.get('data', {})
        # Safety gate: reject if status is unpublished
        if public_data.get('status') not in ['PROVISIONAL_RELEASED', 'CD_FINALIZED']:
            return
        await self.send(text_data=json.dumps(public_data))

    @database_sync_to_async
    def _championship_exists(self):
        from championships.models import Championship
        return Championship.objects.filter(code=self.champ_code).exists()

    @database_sync_to_async
    def _get_released_snapshot(self):
        from scoring.models import Performance, PerformanceStatus
        qs = Performance.objects.filter(
            event_entry__registration__championship__code=self.champ_code,
            status__in=[PerformanceStatus.PROVISIONAL_RELEASED, PerformanceStatus.CD_FINALIZED]
        ).select_related('event_entry__registration__athlete', 'event_entry__event_config')

        if self.event_id != 'all':
            qs = qs.filter(event_entry__event_config_id=self.event_id)

        items = []
        for p in qs.order_by('-final_score', '-provisional_score')[:20]:
            items.append({
                'chest_number': p.chest_number,
                'athlete_name': p.event_entry.display_names,
                'district': p.event_entry.district,
                'score': float(p.final_score if p.final_score is not None else p.provisional_score or 0.0),
                'status': p.status,
                'event_name': p.event_entry.event_config.get_event_type_display(),
            })
        return items
