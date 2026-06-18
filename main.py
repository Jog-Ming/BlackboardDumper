from pathlib import Path
from typing import Final, Any
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup, Tag
from dateutil.parser import parse as parse_date
from pathvalidate import sanitize_filename
from requests import Session
from requests.exceptions import InvalidSchema
from urllib3.contrib.pyopenssl import inject_into_urllib3


def ensure_type[T](value: Any, typ: type[T]) -> T:
    assert isinstance(value, typ)
    return value


def resolve_uri(current: str, uri: str) -> str:
    if (uri_stripped := uri.strip()).startswith('/'):
        return '/'.join(current.split('/')[:3]) + uri_stripped
    return uri_stripped


def remove_tree(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir():
            remove_tree(child)
        else:
            child.chmod(0o777)
            child.unlink()
    path.chmod(0o777)
    path.rmdir()


inject_into_urllib3()
s: Final[Session] = Session()
cache: Final[dict[str, bytes]] = {}
cache_hits: int = 0
cache_misses: int = 0


def get(url: str) -> bytes:
    if url not in cache:
        global cache_misses
        cache_misses += 1
        match (response := s.get(url, allow_redirects=False)).status_code:
            case 301 | 302:
                cache[url] = get(resolve_uri(url, response.headers['Location']))
            case 200:
                cache[url] = response.content
            case _:
                raise RuntimeError(f'Unexpected status code {response.status_code} from {url}')
    else:
        global cache_hits
        cache_hits += 1
    return cache[url]


def download(url: str, path: Path) -> str:
    try:
        r = s.get(url, stream=True)
        if r.status_code == 404:
            return url
        filename: str = path.name
        if path.is_dir():
            path /= (filename := sanitize_filename(unquote(urlparse(r.url).path.split('/')[-1]), '_'))
        with open(path, 'wb') as f:
            for chunk in r.iter_content(1048576):
                f.write(chunk)
        return filename
    except InvalidSchema:
        return url


def main():
    assert s.post('https://shs.blackboardchina.cn/webapps/login/',
                  {'user_id': 'G' + input('G Number: G'), 'password': input('Password: '),
                   'blackboard.platform.security.NonceUtil.nonce.ajax': ensure_type(
                       BeautifulSoup(s.get('https://shs.blackboardchina.cn').content, 'lxml').select_one(
                           'input[name="blackboard.platform.security.NonceUtil.nonce.ajax"]'), Tag)[
                       'value']}).history, 'Wrong G number / password'
    print('Fetching courses, this could take a while...')
    tree: Final[dict[str, dict[str, str]]] = dict(
        (sanitize_filename(': '.join(ensure_type(tag.string, str).split(': ')[1:]), '_'), dict(
            (sanitize_filename(ensure_type(ensure_type(tag.span, Tag).string, str), '_'),
             resolve_uri('https://shs.blackboardchina.cn', ensure_type(tag['href'], str))) for tag in
            BeautifulSoup(get(resolve_uri('https://shs.blackboardchina.cn', ensure_type(tag['href'], str))),
                          'lxml').select('#courseMenuPalette_contents > li > a[href]'))) for tag in BeautifulSoup(
            ensure_type(BeautifulSoup(s.post('https://shs.blackboardchina.cn/webapps/portal/execute/tabs/tabAction',
                                             {'action': 'refreshAjaxModule', 'modId': '_22_1',
                                              'tabId': '_2_1'}).content, 'xml').string, str), 'lxml').select('a[href]'))
    if (root := Path(rf'\\?\{Path('Blackboard').resolve()}')).exists():
        remove_tree(root)
    for k1, k2 in [(k1, k2) for k1, v1 in tree.items() for k2, v2 in ensure_type(v1, dict).items() if
                   len((soup := BeautifulSoup(get(v2), 'lxml')).select('div > #content_listContainer')) + len(
                       soup.select('#announcementList')) != 1]:
        del ensure_type(tree[k1], dict)[k2]
        if not tree[k1]:
            Path(rf'\\?\{Path('Blackboard', k1).resolve()}').mkdir(parents=True)
            del tree[k1]
    q: Final[list[tuple[tuple[str, ...], str]]] = [((k1, k2), v2) for k1, v1 in tree.items() for k2, v2 in
                                                   ensure_type(v1, dict).items()]
    while q:
        print(f'Breadth-first traversal to "{'/'.join(directories := (item := q.pop(0))[0])}"')
        (path := Path(rf'\\?\{Path('Blackboard', *directories).resolve()}')).mkdir(parents=True, exist_ok=True)
        for tag in (soup := BeautifulSoup(get(url := item[1]), 'lxml')).select(
                'div > #content_listContainer > li') or soup.select('#announcementList > li'):
            name: str = sanitize_filename(next(ensure_type(tag.select_one('.item'), Tag).stripped_strings), '_')
            if soup.select_one('#announcementList'):
                name = parse_date(
                    ensure_type(ensure_type(tag.select_one('.details > p > span'), Tag).string, str).split(': ')[-1],
                    ignoretz=True).strftime('%Y%m%d') + ' - ' + name
            link: str | None = resolve_uri(url, ensure_type(a_tag['href'], str)) if (
                a_tag := tag.select_one('.item a[href]')) else None
            content_path: Path = path / name
            if attachment_tags := tag.select('.attachments > li > a[href]'):
                content_path.mkdir(exist_ok=True)
                for attachment_tag in attachment_tags:
                    download(resolve_uri(url, ensure_type(attachment_tag['href'], str)),
                             content_path / sanitize_filename(next(attachment_tag.stripped_strings), '_'))
            include_description: bool = True
            if img_tag := tag.select_one('img.item_icon[alt]'):
                match img_tag['alt']:
                    case 'Content Folder':
                        q.append((directories + (name,), ensure_type(link, str)))
                    case 'File':
                        content_path.mkdir(exist_ok=True)
                        download(resolve_uri(url, ensure_type(link, str)), content_path)
                    case 'Assignment':
                        content_path.mkdir(exist_ok=True)
                        for a_tag in BeautifulSoup(s.get(ensure_type(link, str)).content, 'lxml').select(
                                'a.dwnldBtn[href]'):
                            download(resolve_uri(url, ensure_type(a_tag['href'], str)), content_path)
                    case 'Item':
                        content_path.mkdir(exist_ok=True)
                    case 'Image':
                        content_path.mkdir(exist_ok=True)
                        for image_tag in tag.select('.vtbegenerated > img[src]'):
                            download(resolve_uri(url, ensure_type(image_tag['src'], str)), content_path)
                        include_description = False
                    case 'Video':
                        content_path.mkdir(exist_ok=True)
                        for video_tag in tag.select('video[src]'):
                            download(resolve_uri(url, ensure_type(video_tag['src'], str)), content_path)
                        include_description = False
                    case _:
                        with open(path / f'{name}.url', 'w') as f:
                            f.write(f'[InternetShortcut]\nURL={ensure_type(link, str)}')
            if (description_tag := tag.select_one('.vtbegenerated')) and include_description:
                map(Tag.decompose, description_tag.select('span.contextMenuContainer'))
                content_path.mkdir(exist_ok=True)
                for description_img_tag in description_tag.select('img[src]'):
                    description_img_tag['src'] = download(
                        resolve_uri(url, ensure_type(description_img_tag['src'], str)), content_path)
                for description_a_tag in description_tag.select('a[alt][href]'):
                    description_a_tag['href'] = download(resolve_uri(url, ensure_type(description_a_tag['href'], str)),
                                                         content_path)
                with open(content_path / 'index.html', 'w') as f:
                    f.write(description_tag.prettify())


if __name__ == '__main__':
    main()
    print(f'Cache usage: {cache_hits} hits, {cache_misses} misses ({cache_hits / (cache_hits + cache_misses):.2%})')
