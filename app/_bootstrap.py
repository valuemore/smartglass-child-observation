"""
프로젝트 루트를 sys.path에 추가한다.

모든 Streamlit 페이지 파일 최상단에서 import _bootstrap 으로 호출한다.
이 파일의 __file__ 은 항상 app/ 안에 위치하므로
.parent.parent 가 어디서 import 되든 프로젝트 루트를 정확히 가리킨다.

Streamlit 은 _ 로 시작하는 파일을 페이지로 인식하지 않는다.
"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent  # app/ → 프로젝트 루트
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
