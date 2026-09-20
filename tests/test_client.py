import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.database.session import init_db
from client import AutoMMClient


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    await init_db()


@pytest.mark.asyncio
async def test_automm_client_full_deal_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as ac:
        # Monkey patch AutoMMClient's _request to use this test client
        client = AutoMMClient(base_url="http://test/api/v1")

        async def mock_request(method, path, json=None, headers=None):
            url = f"http://test/api/v1/{path.lstrip('/')}"
            resp = await ac.request(method, url, json=json, headers=headers or {})
            if resp.is_error:
                raise Exception(f"HTTP {resp.status_code}: {resp.text}")
            return resp.json()

        client._request = mock_request

        # 1. Create deal
        deal = await client.create_deal(buyer_id="buyer_discord_111")
        assert deal["id"] is not None
        assert deal["buyer_id"] == "buyer_discord_111"
        assert deal["status"] == "WAITING_FOR_AGREEMENT"
        deal_id = deal["id"]

        # 2. Add seller
        deal = await client.add_seller(deal_id, seller_id="seller_discord_222")
        assert deal["seller_id"] == "seller_discord_222"

        # 3. Set terms
        deal = await client.set_terms(deal_id, amount="0.005", token="ETH", network="SEPOLIA")
        assert deal["amount"] == "0.005"
        assert deal["token"] == "ETH"
        assert deal["network"] == "SEPOLIA"

        # 4. Buyer agrees
        deal = await client.agree(
            deal_id,
            user_id="buyer_discord_111",
            buyer_refund_address="0x742d35Cc6634C0532925a3b844Bc454e4438f44e"
        )
        assert deal["buyer_agreed"] is True
        assert deal["seller_agreed"] is False
        assert deal["terms_locked"] is False

        # 5. Seller agrees -> terms locked
        deal = await client.agree(
            deal_id,
            user_id="seller_discord_222",
            seller_payout_address="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
        )
        assert deal["seller_agreed"] is True
        assert deal["terms_locked"] is True
        assert deal["status"] == "AGREED"

        # 6. Create escrow wallet automatically
        escrow = await client.create_escrow(deal_id, actor_id="buyer_discord_111")
        assert escrow["deposit_address"].startswith("0x")
        assert len(escrow["deposit_address"]) == 42
        assert escrow["status"] == "WAITING_FOR_PAYMENT"

        # 7. Check deposit info
        dep = await client.get_deposit(deal_id)
        assert dep["deposit_address"] == escrow["deposit_address"]

        # 8. Check deal status
        st = await client.get_status(deal_id)
        assert st["deal_id"] == deal_id
        assert st["status"] == "WAITING_FOR_PAYMENT"
