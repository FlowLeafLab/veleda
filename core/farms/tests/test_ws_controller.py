import asyncio
from asgiref.sync import sync_to_async

from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase
from django.urls import reverse
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator

from core.routing import application
from farms.models import (
    Site,
    SiteEntity,
    ControllerComponentType,
    ControllerComponent,
    ControllerMessage,
    ControllerAuthToken,
)


class TestControllerMessage(TransactionTestCase):
    """Test the websockets Controller messages API"""

    @staticmethod
    def init_db():
        """Seed database for WS controller tests"""
        user = get_user_model().objects.create_user("user_a@example.com", "passwd_a")
        site = Site.objects.create(name="Site A", owner=user)
        controller_entity = SiteEntity.objects.create(name="ESP32 - A", site=site)
        esp32 = ControllerComponentType.objects.create(name="ESP32")
        controller_component = ControllerComponent.objects.create(
            site_entity=controller_entity, component_type=esp32
        )
        ControllerAuthToken.objects.create(controller=controller_component)
        return controller_entity

    def setUp(self):
        self.ws_url = "ws-api/v1/farms/controllers/"
        self.controller_entity = self.init_db()
        self.auth_token = (
            f"token_{self.controller_entity.controller_component.auth_token.key}"
        )

    def test_authentication(self):
        """Test that only connections from authenticated controllers are accepted"""

        async def test_body():
            valid_auth_token = self.auth_token
            invalid_auth_token = self.auth_token + "XYZ"

            # Connect as anonymous controller
            communicator = WebsocketCommunicator(
                application,
                self.ws_url,
            )
            connected, _ = await communicator.connect()
            self.assertFalse(connected)
            await communicator.disconnect()

            # Connect as registered user
            communicator = WebsocketCommunicator(
                application,
                self.ws_url,
                subprotocols=[valid_auth_token],
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.disconnect()

            # Connect with invalid token
            communicator = WebsocketCommunicator(
                application,
                self.ws_url,
                subprotocols=[invalid_auth_token],
            )
            connected, _ = await communicator.connect()
            self.assertFalse(connected)

            # Close communicator
            await communicator.disconnect()

        loop = asyncio.get_event_loop()
        loop.run_until_complete(test_body())

    def test_sending_malformed_json(self):
        """Test that the messages are validated"""

        async def test_body():
            # Connect...
            communicator = WebsocketCommunicator(
                application,
                self.ws_url,
                subprotocols=[self.auth_token],
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # ... and send malformed JSON...
            data = "This is not JSON"
            await communicator.send_to(data)
            response = await communicator.receive_json_from()
            self.assertIn("error", response)

            # ... and expect to be disconnected
            output = await communicator.receive_output()
            self.assertEqual("websocket.close", output["type"])

            await communicator.disconnect()

        loop = asyncio.get_event_loop()
        loop.run_until_complete(test_body())

    def test_sending_without_type_in_json(self):
        """Test that the messages are validated"""

        async def test_body():
            # Connect...
            communicator = WebsocketCommunicator(
                application,
                self.ws_url,
                subprotocols=[self.auth_token],
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # ... and send malformed JSON...
            data = {"hello": "there"}
            await communicator.send_json_to(data)
            response = await communicator.receive_json_from()
            self.assertIn("errors", response)

            # ... and expect to be disconnected
            output = await communicator.receive_output()
            self.assertEqual("websocket.close", output["type"])

            await communicator.disconnect()

        loop = asyncio.get_event_loop()
        loop.run_until_complete(test_body())

    def test_sending_valid_message(self):
        """Test that the messages are validated"""

        async def test_body():
            # Connect...
            communicator = WebsocketCommunicator(
                application,
                self.ws_url,
                subprotocols=[self.auth_token],
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # ... and send proper JSON...
            data = {"type": "tel", "number": 12}
            await communicator.send_json_to(data)

            self.assertTrue(await communicator.receive_nothing())

            # ... and expect it to be saved
            saved_message = await database_sync_to_async(
                ControllerMessage.objects.first
            )()
            self.assertDictEqual(data, saved_message.message)

            await communicator.disconnect()

        loop = asyncio.get_event_loop()
        loop.run_until_complete(test_body())

    def test_multiple_connections(self):
        """Test that not more than one WS connection exists per controller"""

        async def test_body():
            # Connect...
            first_communicator = WebsocketCommunicator(
                application,
                self.ws_url,
                subprotocols=[self.auth_token],
            )
            connected, _ = await first_communicator.connect()
            self.assertTrue(connected)

            # Connect...
            second_communicator = WebsocketCommunicator(
                application,
                self.ws_url,
                subprotocols=[self.auth_token],
            )
            connected, _ = await second_communicator.connect()
            self.assertTrue(connected)

            # ... and expect the first one to be disconnected
            output = await first_communicator.receive_output()
            self.assertEqual("websocket.close", output["type"])

            await first_communicator.disconnect()

            # ... and send proper JSON...
            data = {"type": "tel", "number": 99}
            await second_communicator.send_json_to(data)

            self.assertTrue(await second_communicator.receive_nothing())

            # ... and expect it to be saved
            saved_message = await database_sync_to_async(
                ControllerMessage.objects.first
            )()
            self.assertDictEqual(data, saved_message.message)

            await second_communicator.disconnect()

        loop = asyncio.get_event_loop()
        loop.run_until_complete(test_body())

    def test_rest_api_to_ws_connection(self):
        """Test that the messages are validated"""

        async def test_body():
            client = Client()

            # Connect...
            communicator = WebsocketCommunicator(
                application,
                self.ws_url,
                subprotocols=[self.auth_token],
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # Log in and send command over REST API
            logged_in = await sync_to_async(client.login)(
                username="user_a@example.com", password="passwd_a"
            )
            self.assertTrue(logged_in)
            data = {
                "type": "cmd",
                "peripheral": {"add": [{"name": "led33", "type": "LED", "pin": 33}]},
            }
            response = await sync_to_async(client.post)(
                reverse("controller-command", kwargs={"pk": self.controller_entity.id}),
                data=data,
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)

            # Check that data was received
            received_data = await communicator.receive_json_from()
            self.assertDictEqual(data, received_data)

            await communicator.disconnect()

        loop = asyncio.get_event_loop()
        loop.run_until_complete(test_body())
