"""Installed, offline help and original open-source notices."""
from dataclasses import dataclass
from html import escape, unescape
from pathlib import Path
import json
import re

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from . import __version__


@dataclass
class Article:
    title: str
    text: str
    format: str = 'plain'
    path: Path | None = None


def guide_articles(root):
    path = root/'docs/사용안내.html'
    text = path.read_text(encoding='utf-8')
    sections = re.findall(r'<section[^>]*>\s*<h2[^>]*>(.*?)</h2>(.*?)</section>', text, re.S)
    articles = [Article(unescape(re.sub('<[^>]+>', '', title)), f'<h2>{title}</h2>{body}', 'html', path)
                for title, body in sections]
    return sorted(articles, key=lambda article: article.title != '처음 시작하기')


def license_articles(root):
    folder = root/'LICENSES'
    preferred = [('README.md', '라이선스와 소스 제공 안내'), ('AGPL-3.0.txt', 'ADF · PyMuPDF / MuPDF — AGPL'),
                 ('LGPL-3.0.txt', 'Qt / PySide6 — LGPL'), ('GPL-3.0.txt', 'LGPL이 참조하는 GPL'),
                 ('Python-LICENSE.txt', 'Python'), ('Pillow/licenses/LICENSE', 'Pillow'),
                 ('fonttools/licenses/LICENSE', 'fontTools'),
                 ('PyInstaller/licenses/COPYING.txt', 'PyInstaller · 부트로더 예외'),
                 ('Inno-Setup-LICENSE.txt', 'Inno Setup'),
                 ('PaddleOCR-APACHE-2.0.txt', 'OCR 모델 · PaddlePaddle / RapidAI'),
                 ('onnxruntime/onnxruntime/LICENSE', 'ONNX Runtime'),
                 ('onnxruntime/onnxruntime/ThirdPartyNotices.txt', 'ONNX Runtime · 외부 구성 요소'),
                 ('shapely/licenses/LICENSE_GEOS', 'GEOS · LGPL-2.1')]
    used = {name for name, _ in preferred}
    original_names = {}
    for index in folder.rglob('INDEX.json'):
        for stored, original in json.loads(index.read_text(encoding='utf-8')).items():
            original_names[(index.parent/stored).relative_to(folder).as_posix()] = original
    others = sorted(path.relative_to(folder).as_posix() for path in folder.rglob('*')
                    if path.is_file() and path.name not in ('METADATA.txt', 'build-manifest.json', 'index.html', 'INDEX.json')
                    and path.relative_to(folder).as_posix() not in used)
    extra = []
    for name in others:
        original = original_names.get(name)
        if original:
            # The upstream collector also preserves examples named "license*".
            # Keep those in the source archive, outside the notice reader.
            if Path(original).suffix.lower() in ('.png', '.cpp', '.h', '.pro', '.qrc', '.qdoc'):
                continue
            _, _, relative = original.partition('/')
            component = name.split('/')[1]
            extra.append((name, component+' · '+relative))
        else:
            extra.append((name, name))
    result = []
    for name, title in preferred + extra:
        path = folder/name
        if path.is_file():
            result.append(Article(title, path.read_text(encoding='utf-8-sig', errors='replace'),
                                  'markdown' if path.suffix == '.md' else 'plain', path))
    return result


class HelpPage(QWidget):
    sourcesRequested = Signal()

    def __init__(self, articles, root, parent=None):
        super().__init__(parent)
        self.articles = articles
        self.root = root.resolve()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 8)
        self.search = QLineEdit()
        self.search.setPlaceholderText('목차와 내용 검색')
        self.search.setAccessibleName('도움말 검색')
        outer.addWidget(self.search)
        row = QHBoxLayout()
        self.topics = QListWidget()
        self.topics.setAccessibleName('도움말 목차')
        self.topics.setFixedWidth(235)
        self.topics.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.topics.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.topics.setStyleSheet('QListWidget::item { padding: 9px 7px; margin: 1px 0; }')
        self.browser = QTextBrowser()
        self.browser.setAccessibleName('도움말 본문')
        self.browser.setOpenLinks(False)
        self.browser.setStyleSheet('QTextBrowser { background: white; border: 1px solid #e0e4eb; border-radius: 8px; padding: 16px; }')
        self.browser.document().setDefaultStyleSheet(
            'h2 { font-size: 19pt; color: #222c3b; margin-bottom: 18px; }'
            'h1 { font-size: 20pt; } p, li { line-height: 155%; }'
            'p { margin-bottom: 12px; } kbd, code { background-color: #eef1f6; }'
            'a { color: #454950; text-decoration: underline; }')
        row.addWidget(self.topics)
        row.addWidget(self.browser, 1)
        outer.addLayout(row, 1)
        for index, article in enumerate(articles):
            item = QListWidgetItem(article.title)
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(article.title)
            self.topics.addItem(item)
        self.search.textChanged.connect(self.filter_topics)
        self.topics.currentItemChanged.connect(self.show_article)
        self.browser.anchorClicked.connect(self.open_link)
        self.topics.setCurrentRow(0)

    def filter_topics(self, text):
        query = text.casefold().strip()
        visible = []
        for index, article in enumerate(self.articles):
            item = self.topics.item(index)
            match = not query or query in (article.title+' '+unescape(re.sub('<[^>]+>', ' ', article.text))).casefold()
            item.setHidden(not match)
            if match:
                visible.append(item)
        current = self.topics.currentItem()
        if not visible:
            self.browser.setPlainText('검색 결과가 없습니다. 다른 검색어를 입력해 주세요.')
        elif current is None or current.isHidden():
            self.topics.setCurrentItem(visible[0])
        else:
            self.show_article(current)

    def show_article(self, item, previous=None):
        if item is None:
            return
        article = self.articles[item.data(Qt.ItemDataRole.UserRole)]
        if article.path:
            self.browser.document().setBaseUrl(QUrl.fromLocalFile(str(article.path.parent)+'/'))
        if article.format == 'html':
            self.browser.setHtml(article.text)
        elif article.format == 'markdown':
            self.browser.setMarkdown(article.text)
        else:
            self.browser.setPlainText(article.text)
        self.browser.verticalScrollBar().setValue(0)

    def open_link(self, url):
        if url.isRelative():
            url = self.browser.document().baseUrl().resolved(url)
        if url.scheme() in ('https', 'http'):
            QDesktopServices.openUrl(url)
        elif url.isLocalFile():
            path = Path(url.toLocalFile()).resolve()
            if not path.is_relative_to(self.root):
                return
            if path.is_relative_to(self.root/'SOURCES'):
                self.sourcesRequested.emit()
                return
            for index, article in enumerate(self.articles):
                if article.path and article.path.resolve() == path:
                    self.search.clear()
                    self.topics.setCurrentRow(index)
                    return
            if path.is_dir() or path.suffix.lower() in ('.zip', '.html', '.txt', '.md'):
                QDesktopServices.openUrl(url)


class HelpDialog(QDialog):
    """One topic per independent window, matching the Help menu order."""
    sourcesRequested = Signal()

    def __init__(self, root, section, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.section = section
        titles = {'guide': '사용 안내', 'licenses': '오픈소스 라이선스', 'sources': '소스코드'}
        descriptions = {
            'guide': '목차에서 기능을 고르거나 검색해 사용 방법을 확인하세요.',
            'licenses': '구성 요소별 고지와 라이선스 원문을 인터넷 연결 없이 읽을 수 있습니다.',
            'sources': '설치 EXE 하나에 프로그램·사용안내·라이선스·소스가 모두 포함되어 있습니다.',
        }
        self.setWindowTitle('ADF · '+titles[section])
        width, height = (980, 730)
        self.resize(width, height)
        self.setMinimumSize(680, 460)
        if self.screen():
            size = self.screen().availableGeometry().size()
            self.resize(min(width, size.width()-40), min(height, size.height()-60))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        heading = QHBoxLayout()
        title = QLabel(titles[section])
        title.setObjectName('introTitle')
        heading.addWidget(title)
        heading.addStretch()
        heading.addWidget(QLabel(f'버전 {__version__}'))
        outer.addLayout(heading)
        description = QLabel(descriptions[section])
        description.setWordWrap(True)
        description.setObjectName('muted')
        outer.addWidget(description)
        if section == 'sources':
            outer.addWidget(self.build_sources(), 1)
        else:
            articles = guide_articles(self.root) if section == 'guide' else license_articles(self.root)
            self.page = HelpPage(articles, self.root)
            self.page.sourcesRequested.connect(self.sourcesRequested)
            outer.addWidget(self.page, 1)
        buttons = QHBoxLayout()
        if section == 'guide':
            original = QPushButton('안내 HTML 열기')
            original.setToolTip('브라우저에서 사용안내를 읽거나 인쇄합니다.')
            original.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root/'docs/사용안내.html'))))
            buttons.addWidget(original)
        buttons.addStretch()
        close = QPushButton('닫기')
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        outer.addLayout(buttons)

    def build_sources(self):
        sources = QWidget()
        source_layout = QVBoxLayout(sources)
        source_layout.setContentsMargins(0, 16, 0, 16)
        self.source_files = [f'ADF-Source-{__version__}.zip', f'ADF-ThirdParty-Sources-{__version__}.zip']
        self.sources_path = self.root/'SOURCES'
        self.sources_available = all((self.sources_path/name).is_file() for name in self.source_files)
        state = QLabel('압축 프로그램이나 별도 다운로드 없이 아래에서 소스 파일을 읽을 수 있습니다. '
                       '필요할 때만 선택한 소스를 다른 위치에 저장하세요.' if self.sources_available else
                       '이 개발 실행 환경에는 소스 묶음이 없습니다. 직원에게 전달하는 정식 설치 EXE에는 소스가 포함됩니다.')
        state.setWordWrap(True)
        source_layout.addWidget(state)
        self.source_browser = None
        if self.sources_available:
            from .source_widgets import SourceBrowser
            self.source_browser = SourceBrowser(self.sources_path, self.source_files)
            source_layout.addWidget(self.source_browser, 1)
        else:
            source_layout.addStretch()
        return sources
