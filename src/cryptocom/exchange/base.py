import asyncio

from dataclasses import dataclass

from .api import ApiProvider
from .enums import (
    Symbol, Period, Depth, PeriodWebSocket, OrderSide, OrderStatus, OrderType
)


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class Exchange:
    def __init__(self, api: ApiProvider = None):
        self.api = api or ApiProvider(auth_required=False)

    async def get_symbols(self):
        """List all available market symbols."""
        return await self.api.get('symbols')

    async def get_tickers(self):
        """Get tickers in all available markets."""
        response = await self.api.ws_request(
            {'event': 'req', 'params': {'channel': 'review'}})
        return response['data']

    async def get_ticker(self, symbol: Symbol):
        return (await self.get_tickers()).get(symbol.value)

    async def get_candles(self, symbol: Symbol, period: Period):
        """Get k-line data over a specified period."""
        data = await self.api.get(
            'klines', {'symbol': symbol.value, 'period': period.value})
        for candle in reversed(data):
            yield Candle(*candle)

    async def get_trades(self, symbol: Symbol):
        """Get last 200 trades in a specified market."""
        return await self.api.get('trades', {'symbol': symbol.value})

    async def get_prices(self):
        """Get latest execution price for all markets."""
        return await self.api.get('ticker/price')

    async def get_price(self, symbol: Symbol):
        return float((await self.get_prices())[symbol.value])

    async def get_orderbook(self, symbol: Symbol, depth: Depth = Depth.LOW):
        """Get the order book for a particular market."""
        data = await self.api.get(
            'depth', {'symbol': symbol.value, 'type': depth.value})
        return data['tick']

    async def listen_candles(self, symbol: Symbol, period: Period):
        period = PeriodWebSocket[period.name].value
        channel = {
            'event': 'sub',
            'params': {'channel': f'market_{symbol.value}_kline_{period}'}
        }
        prev_id = None
        async for data in self.api.ws_listen(channel):
            if 'ping' in data:
                continue
            candle = data['tick']
            if candle['id'] == prev_id:
                continue
            prev_id = candle['id']
            yield Candle(
                candle['id'], candle['open'], candle['high'],
                candle['low'], candle['close'], candle['vol']
            )

    async def listen_trades(self, symbol: Symbol):
        channel = {
            'event': 'sub',
            'params': {'channel': f'market_{symbol.value}_trade_ticker'}
        }
        async for data in self.api.ws_listen(channel):
            if 'ping' in data:
                continue
            yield data['tick']['data']

    async def listen_order_book(
            self, symbol: Symbol, depth: Depth = Depth.LOW):
        channel = {
            'event': 'sub',
            'params': {
                'channel': f'market_{symbol.value}_depth_{depth.value}',
                'asks': 150,
                'buys': 150
            }
        }
        async for data in self.api.ws_listen(channel):
            if 'ping' in data:
                continue
            data['tick']['bids'] = data['tick'].pop('buys')
            yield data['tick']


class Account:
    def __init__(
            self, *, api_key: str = '', api_secret: str = '',
            from_env: bool = False, api: ApiProvider = None):
        if not api and not (api_key and api_secret) and not from_env:
            raise ValueError(
                'Pass ApiProvider or api_key with api_secret or from_env')
        self.api = api or ApiProvider(
            api_key=api_key, api_secret=api_secret, from_env=from_env)

    async def get_balance(self):
        return await self.api.post('account')

    async def get_orders(
            self, symbol: Symbol, page: int = 1, page_size: int = 20):
        data = await self.api.post('allOrders', {
            'symbol': symbol.value,
            'pageSize': page_size,
            'page': page
        })
        return data.get('orderList') or []

    async def get_open_orders(
            self, symbol: Symbol, page: int = 1, page_size: int = 20):
        data = await self.api.post('openOrders', {
            'symbol': symbol.value,
            'pageSize': page_size,
            'page': page
        })
        return data.get('resultList') or []

    async def get_trades(
            self, symbol: Symbol, page: int = 1, page_size: int = 20):
        data = await self.api.post('myTrades', {
            'symbol': symbol.value,
            'pageSize': page_size,
            'page': page
        })
        return data.get('resultList') or []

    async def create_order(
            self, symbol: Symbol, side: OrderSide, type_: OrderType,
            volume: float, price: float = 0) -> int:
        data = {
            'symbol': symbol.value, 'side': side.value,
            'type': type_.value, 'volume': volume,
        }

        if price:
            if type_ == OrderType.MARKET:
                raise ValueError(
                    "Error, MARKET execution do not support price value")
            data['price'] = price

        resp = await self.api.post('order', data)
        return int(resp['order_id'])

    async def buy_limit(self, symbol: Symbol, volume: float, price: float):
        return await self.create_order(
            symbol, OrderSide.BUY, OrderType.LIMIT, volume, price
        )

    async def sell_limit(self, symbol: Symbol, volume: float, price: float):
        return await self.create_order(
            symbol, OrderSide.SELL, OrderType.LIMIT, volume, price
        )

    async def wait_for_status(
            self, order_id: int, symbol: Symbol, statuses, delay: int = 0.2):
        order = await self.get_order(order_id, symbol)
        statuses = (
            OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED
        )
        while order['status'] not in statuses:
            await asyncio.sleep(delay)
            order = await self.get_order(order_id, symbol)

    async def buy_market(
            self, symbol: Symbol, volume: float, wait_for_fill=True):
        order_id = await self.create_order(
            symbol, OrderSide.BUY, OrderType.MARKET, volume
        )
        if wait_for_fill:
            await self.wait_for_status(order_id, symbol, (
                OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED
            ))

        return order_id

    async def sell_market(
            self, symbol: Symbol, volume: float, wait_for_fill=True):
        order_id = await self.create_order(
            symbol, OrderSide.SELL, OrderType.MARKET, volume
        )

        if wait_for_fill:
            await self.wait_for_status(order_id, symbol, (
                OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED
            ))

        return order_id

    async def get_order(self, order_id: int, symbol: Symbol):
        data = await self.api.post(
            'showOrder', {'order_id': order_id, 'symbol': symbol.value})
        return data['order_info']

    async def cancel_order(
            self, order_id: int, symbol: Symbol, wait_for_cancel=True):
        await self.api.post(
            'orders/cancel', {'order_id': order_id, 'symbol': symbol.value})

        if not wait_for_cancel:
            return

        await self.wait_for_status(order_id, symbol, (
            OrderStatus.CANCELED, OrderStatus.EXPIRED
        ))

    async def cancel_open_orders(self, symbol: Symbol):
        return await self.api.post('cancelAllOrders', {'symbol': symbol.value})