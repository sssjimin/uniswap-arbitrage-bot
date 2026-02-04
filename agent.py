import os
import time
import requests
from web3 import Web3
from uniswap import Uniswap
from dotenv import load_dotenv
from xai_sdk import Client  # xAI SDK 클라이언트 import 추가
from xai_sdk.chat import user, system  # 챗 역할 헬퍼 import 추가

load_dotenv()

# 연결 설정: Infura로 Ethereum 네트워크 연결
infura_url = os.getenv('INFURA_URL')
print(f"DEBUG: INFURA_URL 값 = '{infura_url}' (타입: {type(infura_url)})")  # 디버깅

if infura_url is None or not isinstance(infura_url, str) or not infura_url.startswith('https://'):
    print("ERROR: INFURA_URL이 유효하지 않습니다.")
    exit()

w3 = Web3(Web3.HTTPProvider(infura_url))
if not w3.is_connected():
    print("Ethereum 연결 실패 – INFURA_URL 확인하세요")
    exit()

# endpoint_uri 추출 (이미지 사례처럼 URL 문자열 별도 정의 – 버그 방지)
provider_uri = w3.provider.endpoint_uri if hasattr(w3.provider, 'endpoint_uri') else infura_url
print(f"DEBUG: provider_uri = '{provider_uri}'")  # 확인

# netid 수동 설정 – net.version 대신 chain_id 사용 (오류 우회)
try:
    chain_id = w3.eth.chain_id
    print(f"DEBUG: 체인 ID = {chain_id}")  # mainnet: 1, Sepolia: 11155111
    netid = chain_id
except Exception as e:
    print(f"chain_id 오류: {e}")
    netid = 1  # mainnet 기본, 테스트넷 11155111로 변경

address = os.getenv('WALLET_ADDRESS')
private_key = os.getenv('PRIVATE_KEY')
uniswap = Uniswap(address=address, private_key=private_key, version=3, provider=w3)
uniswap.netid = netid

# Uniswap 가격 쿼리 함수 (예: 1 ETH가 USDC로 얼마?)
def get_uniswap_price(token_in, token_out, qty):
    return uniswap.get_price_input(token_in, token_out, qty)

# 다른 DEX나 외부 가격 가져오기 (예: CoinGecko API로 ETH 가격 – USDC 근사)
def get_other_dex_price(token_symbol):
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={token_symbol}&vs_currencies=usd"
    response = requests.get(url)
    if response.ok:
        return response.json()[token_symbol]['usd']
    else:
        print("CoinGecko API 오류")
        return None
 
# Grok LLM 호출 함수 (xAI API 문서 기반: https://docs.x.ai/docs/tutorial)
def ask_grok(query):
    api_key = os.getenv("XAI_API_KEY")  # .env에서 XAI_API_KEY 불러옴
    if not api_key:
        print("XAI_API_KEY가 없습니다. .env 파일 확인하세요.")
        return "오류 – 거래 안 함"

    client = Client(api_key=api_key)  # Client 초기화 (import 추가로 사용 가능)

    try:
        response = client.chat.completions.create(
            model="grok-4",  # Grok 모델 지정
            messages=[
                system("You are Grok, a highly intelligent, helpful AI assistant."),  # 시스템 프롬프트 (import 추가로 system 사용)
                user(query)  # 사용자 쿼리 (import 추가로 user 사용)
            ],
            temperature=0.7  # 응답 창의성 조절 (문서 예시 기반)
        )
        return response.choices[0].message.content  # 응답 content 추출
    except Exception as e:
        print(f"Grok API 호출 실패: {e}")
        return "오류 – 거래 안 함"

# 메인 루프: 1분 간격으로 가격 모니터링
ETH = w3.to_checksum_address('0x0000000000000000000000000000000000000000')  # ETH 주소
USDC = w3.to_checksum_address('0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48')  # USDC 주소
while True:
    uniswap_price = get_uniswap_price(ETH, USDC, 10**18) / 10**6  # 1 ETH to USDC 가격 (USDC 단위 정규화)
    other_price = get_other_dex_price('ethereum')  # CoinGecko ETH/USD 가격
    if other_price:
        diff = abs(uniswap_price - other_price) / min(uniswap_price, other_price) * 100  # 차이 퍼센트 계산
        if diff > 0.5:  # 임계값 0.5% 이상 시 판단 (조정 가능)
            gas_estimate = w3.eth.gas_price  # 현재 가스비 추정
            query = f"가격 차이 {diff}% 발견: 거래할 가치 있나? 가스비 {gas_estimate / 10**9} Gwei, 슬리피지(가격 미끄러짐) 고려해서 답해."
            decision = ask_grok(query)
            if '예' in decision.lower() or '거래' in decision:  # LLM이 긍정하면 거래
                # 거래 실행 예시 (0.1 ETH 스왑 – 소액 테스트)
                try:
                    tx = uniswap.make_trade(ETH, USDC, 10**17)  # 0.1 ETH to USDC
                    signed_tx = w3.eth.account.sign_transaction(tx, private_key)
                    tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
                    print(f"거래 성공: {tx_hash.hex()}")
                except Exception as e:
                    print(f"거래 실패: {e}")
            else:
                print("LLM 판단: 거래 안 함")
    else:
        print("외부 가격 데이터 못 가져옴")
    time.sleep(60)  # 1분 대기 – 시장 변화 체크