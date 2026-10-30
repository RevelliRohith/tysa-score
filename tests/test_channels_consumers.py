import pytest
import json
import uuid
from decimal import Decimal
import datetime
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from config.asgi import application
from championships.models import Championship, EventConfiguration, EventType, AgeCategory
from accounts.models import ScopedRole, RoleChoices
from athletes.models import Athlete
from registrations.models import ChampionshipRegistration, EventEntry, RegistrationStatus
from scoring.models import Performance, PerformanceStatus

User = get_user_model()

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestChannelsConsumers:
    """
    Verifies websocket authorization, scoped groups, initial snapshots,
    unpublished score suppression, and cross-championship isolation.
    """

    async def setup_test_data(self):
        uid = uuid.uuid4().hex[:6]
        # Championship 1
        champ1 = await Championship.objects.acreate(
            name=f"Telangana State Yogasana {uid}",
            code=f"TYSA-WS-{uid}",
            year=2026,
            start_date=datetime.date(2026, 11, 20),
            end_date=datetime.date(2026, 11, 22),
            registration_start=datetime.date(2026, 10, 1),
            registration_end=datetime.date(2026, 11, 15),
            age_cutoff_date=datetime.date(2026, 12, 31),
            venue_name="Saroornagar Stadium",
            is_rules_preset_verified=True
        )

        # Championship 2 (for cross-championship isolation testing)
        champ2 = await Championship.objects.acreate(
            name=f"Andhra State Yogasana {uid}",
            code=f"AP-WS-{uid}",
            year=2026,
            start_date=datetime.date(2026, 11, 20),
            end_date=datetime.date(2026, 11, 22),
            registration_start=datetime.date(2026, 10, 1),
            registration_end=datetime.date(2026, 11, 15),
            age_cutoff_date=datetime.date(2026, 12, 31),
            venue_name="Vijayawada Stadium",
            is_rules_preset_verified=True
        )

        cat = await AgeCategory.objects.acreate(code=f"JR-{uid}", name="Junior WS", min_age=14, max_age=18)

        ev1 = await EventConfiguration.objects.acreate(
            championship=champ1,
            event_type=EventType.TRADITIONAL,
            total_asanas=7
        )
        await ev1.age_categories.aset([cat])

        ev2 = await EventConfiguration.objects.acreate(
            championship=champ2,
            event_type=EventType.TRADITIONAL,
            total_asanas=7
        )
        await ev2.age_categories.aset([cat])

        # Users
        stage_manager = await User.objects.acreate_user(email=f"sm_{uid}@tysa.org", password="pass", full_name="Stage Manager")
        await ScopedRole.objects.acreate(
            user=stage_manager,
            role=RoleChoices.STAGE_MANAGER,
            championship=champ1
        )

        unauthorized_user = await User.objects.acreate_user(email=f"unauth_{uid}@tysa.org", password="pass", full_name="Regular Athlete")

        # Athletes & Performances for Champ 1
        ath1 = await Athlete.objects.acreate(
            first_name="Pooja", last_name="WS", dob=datetime.date(2010, 5, 10), gender="F", district="Hyderabad"
        )
        reg1 = await ChampionshipRegistration.objects.acreate(
            athlete=ath1, championship=champ1, registration_number=f"REG-WS-1-{uid}", status=RegistrationStatus.DISTRICT_APPROVED
        )
        entry1 = await EventEntry.objects.acreate(
            registration=reg1, event_config=ev1, age_category=cat, gender="F", chest_number=f"W-{uid[:3]}1"
        )
        perf_published = await Performance.objects.acreate(
            event_entry=entry1,
            round_type="PRELIMINARY",
            status=PerformanceStatus.CD_FINALIZED,
            provisional_score=Decimal('18.25'),
            final_score=Decimal('18.25')
        )

        ath2 = await Athlete.objects.acreate(
            first_name="Ravi", last_name="WS", dob=datetime.date(2010, 6, 12), gender="M", district="Warangal"
        )
        reg2 = await ChampionshipRegistration.objects.acreate(
            athlete=ath2, championship=champ1, registration_number=f"REG-WS-2-{uid}", status=RegistrationStatus.DISTRICT_APPROVED
        )
        entry2 = await EventEntry.objects.acreate(
            registration=reg2, event_config=ev1, age_category=cat, gender="M", chest_number=f"W-{uid[:3]}2"
        )
        perf_unpublished = await Performance.objects.acreate(
            event_entry=entry2,
            round_type="PRELIMINARY",
            status=PerformanceStatus.SUBMITTED,
            provisional_score=Decimal('15.00')
        )

        # Athlete & Performance for Champ 2 (should NEVER appear in Champ 1)
        ath_other = await Athlete.objects.acreate(
            first_name="Kiran", last_name="AP", dob=datetime.date(2010, 1, 1), gender="M", district="Guntur"
        )
        reg_other = await ChampionshipRegistration.objects.acreate(
            athlete=ath_other, championship=champ2, registration_number=f"REG-AP-1-{uid}", status=RegistrationStatus.DISTRICT_APPROVED
        )
        entry_other = await EventEntry.objects.acreate(
            registration=reg_other, event_config=ev2, age_category=cat, gender="M", chest_number=f"AP-{uid[:3]}9"
        )
        perf_other_champ = await Performance.objects.acreate(
            event_entry=entry_other,
            round_type="PRELIMINARY",
            status=PerformanceStatus.CD_FINALIZED,
            provisional_score=Decimal('19.99'),
            final_score=Decimal('19.99')
        )

        return {
            'champ1': champ1,
            'champ2': champ2,
            'ev1': ev1,
            'stage_manager': stage_manager,
            'unauthorized_user': unauthorized_user,
            'entry1': entry1,
            'entry2': entry2,
            'entry_other': entry_other,
            'perf_published': perf_published,
            'perf_unpublished': perf_unpublished,
            'perf_other_champ': perf_other_champ,
        }

    async def test_stage_consumer_rejects_anonymous_access(self):
        """Anonymous websocket connections to private stage coordination must be closed with 4401."""
        communicator = WebsocketCommunicator(application, "/ws/stage/TYSA-WS-01/1/")
        communicator.scope['user'] = AnonymousUser()
        connected, close_code = await communicator.connect()
        assert not connected
        assert close_code == 4401

    async def test_stage_consumer_rejects_unauthorized_user(self):
        """Authenticated users lacking required scoped role must be closed with 4401."""
        data = await self.setup_test_data()
        communicator = WebsocketCommunicator(application, f"/ws/stage/{data['champ1'].code}/1/")
        communicator.scope['user'] = data['unauthorized_user']
        connected, close_code = await communicator.connect()
        assert not connected
        assert close_code == 4401

    async def test_stage_consumer_authorized_connection_and_broadcast(self):
        """Authorized stage manager connects successfully and receives private stage updates."""
        data = await self.setup_test_data()
        communicator = WebsocketCommunicator(application, f"/ws/stage/{data['champ1'].code}/1/")
        communicator.scope['user'] = data['stage_manager']
        connected, _ = await communicator.connect()
        assert connected

        # Simulate stage event broadcast
        from channels.layers import get_channel_layer
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"stage_{data['champ1'].code}_1",
            {
                "type": "stage_update",
                "data": {"current_chest": "W-101", "stage_state": "ON_STAGE"}
            }
        )

        response = await communicator.receive_json_from()
        assert response["current_chest"] == "W-101"
        assert response["stage_state"] == "ON_STAGE"
        await communicator.disconnect()

    async def test_live_results_consumer_snapshot_and_unpublished_exclusion(self):
        """
        Public results consumer accepts connection, immediately returns snapshot containing
        only published scores, and excludes unpublished / draft scores.
        """
        data = await self.setup_test_data()
        communicator = WebsocketCommunicator(
            application,
            f"/ws/results/{data['champ1'].code}/{data['ev1'].id}/"
        )
        connected, _ = await communicator.connect()
        assert connected

        initial_msg = await communicator.receive_json_from()
        assert initial_msg["type"] == "snapshot"
        snapshot = initial_msg["data"]

        # Only entry1 (CD_FINALIZED) should be present
        chest_numbers = [item["chest_number"] for item in snapshot]
        assert data['entry1'].chest_number in chest_numbers
        # entry2 is SUBMITTED (unpublished) -> must NOT be present
        assert data['entry2'].chest_number not in chest_numbers
        # entry_other is from another championship -> must NOT be present (cross-championship isolation)
        assert data['entry_other'].chest_number not in chest_numbers

        await communicator.disconnect()

    async def test_live_results_consumer_nonexistent_championship_rejection(self):
        """Connecting to results of a non-existent championship closes with 4404."""
        communicator = WebsocketCommunicator(application, "/ws/results/NONEXISTENT-CHAMP/all/")
        connected, close_code = await communicator.connect()
        assert not connected
        assert close_code == 4404

    async def test_live_results_consumer_broadcast_filtering(self):
        """Consumer ignores unpublished broadcasts and forwards released scores."""
        data = await self.setup_test_data()
        communicator = WebsocketCommunicator(
            application,
            f"/ws/results/{data['champ1'].code}/{data['ev1'].id}/"
        )
        connected, _ = await communicator.connect()
        assert connected
        # Read snapshot
        await communicator.receive_json_from()

        from channels.layers import get_channel_layer
        channel_layer = get_channel_layer()
        group_name = f"results_{data['champ1'].code}_{data['ev1'].id}"

        # 1. Send an unpublished broadcast (should be ignored by consumer)
        await channel_layer.group_send(
            group_name,
            {
                "type": "results_update",
                "data": {"chest_number": data['entry2'].chest_number, "status": "SUBMITTED", "score": 15.0}
            }
        )
        assert await communicator.receive_nothing(timeout=0.2)

        # 2. Send an officially released broadcast (should be forwarded to client)
        await channel_layer.group_send(
            group_name,
            {
                "type": "results_update",
                "data": {"chest_number": data['entry1'].chest_number, "status": "CD_FINALIZED", "score": 18.25}
            }
        )
        update = await communicator.receive_json_from()
        assert update["chest_number"] == data['entry1'].chest_number
        assert update["status"] == "CD_FINALIZED"

        await communicator.disconnect()
