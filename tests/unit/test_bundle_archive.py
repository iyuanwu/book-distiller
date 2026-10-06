"""Untrusted archive boundary tests without AI or Library initialization."""
from pathlib import Path
from uuid import uuid4
import hashlib
import json
import stat
import zipfile
import pytest
from book_distiller.bundle.archive import BundleLimits, validate_archive, safe_name, content_hash
from book_distiller.bundle.models import BookBundleManifest, BundleFile
from book_distiller.core.errors import StorageError


def fixture_manifest(payload=b'hello'):
    uid=uuid4()
    return BookBundleManifest(created_by_version='test',book_id=uid,edition_id=uuid4(),title='test',source_sha256='0'*64,
        source_mode='normalized_only',source_included=False,normalized_generation_id=uid,classification_id=uid,
        classification_hash='0'*64,chapter_generation_ids={},book_generation_id=uid,verification_generation_id=uid,
        reader_generation_id=uid,human_state_hash='0'*64,book_rule_hash='0'*64,quality_gate='pass',versions={},pointers={},
        files=[BundleFile(relative_path='manifest.json',sha256=hashlib.sha256(payload).hexdigest(),size=len(payload),category='metadata')],bundle_content_hash='0'*64)


def archive(path,payload=b'hello',mutate=None,extras=()):
    m=fixture_manifest(payload).model_dump(mode='json')
    if mutate:mutate(m)
    m['bundle_content_hash']=content_hash(m)
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('bundle_manifest.json',json.dumps(m))
        z.writestr('manifest.json',payload)
        for name,data in extras:z.writestr(name,data)
    return path


@pytest.mark.parametrize('name',['../x','a/../../x','/tmp/x','C:\\x','C:/x','a\\b','a//b','./x','a/./x','a/../x','a.','a ','a\n','e\u0301',''])
def test_unsafe_paths(name):
    with pytest.raises(StorageError):safe_name(name)


@pytest.mark.parametrize('name',['../x','/tmp/x','C:\\x','undeclared','MANIFEST.JSON','manifest.json'])
def test_reject_extra_duplicate_traversal(tmp_path,name):
    if name=='manifest.json':
        with pytest.warns(UserWarning,match='Duplicate name'):
            path=archive(tmp_path/'bad.zip',extras=[(name,b'x')])
    else:
        path=archive(tmp_path/'bad.zip',extras=[(name,b'x')])
    with pytest.raises(StorageError):validate_archive(path,tmp_path/'out')
    assert not (tmp_path/'x').exists()


@pytest.mark.parametrize('kind',[stat.S_IFLNK,stat.S_IFIFO,stat.S_IFSOCK,stat.S_IFCHR,stat.S_IFBLK])
def test_reject_nonregular_zip_entries(tmp_path,kind):
    info=zipfile.ZipInfo('extra');info.create_system=3;info.external_attr=(kind|0o777)<<16
    path=archive(tmp_path/'bad.zip',extras=[(info,b'/tmp/target')])
    with pytest.raises(StorageError,match='UNSAFE_ENTRY'):validate_archive(path,tmp_path/'out')


@pytest.mark.parametrize('change',[
    lambda m:m.update(bundle_version='99'),
    lambda m:m['files'][0].update(sha256='1'*64),
    lambda m:m['files'][0].update(size=999),
    lambda m:m['files'].append(dict(m['files'][0])),
    lambda m:m['files'].append(dict(m['files'][0],relative_path='missing')),
    lambda m:m.update(source_included=True),
])
def test_bad_manifest_inventory_checksum(tmp_path,change):
    with pytest.raises(StorageError):validate_archive(archive(tmp_path/'bad.zip',mutate=change),tmp_path/'out')


@pytest.mark.parametrize('limits',[BundleLimits(max_files=1),BundleLimits(max_total_bytes=5),BundleLimits(max_file_bytes=4),BundleLimits(max_compression_ratio=1),BundleLimits(max_manifest_bytes=1)])
def test_resource_limits(tmp_path,limits):
    with pytest.raises(StorageError,match='LIMIT|OVERSIZED'):validate_archive(archive(tmp_path/'large.zip',b'a'*10000),tmp_path/'out',limits)


def test_read_only_hash_and_no_self_hash(tmp_path):
    path=archive(tmp_path/'ok.zip')
    before=path.read_bytes()
    m=validate_archive(path,tmp_path/'out')
    assert path.read_bytes()==before and (tmp_path/'out/manifest.json').read_bytes()==b'hello'
    value=m.model_dump(mode='json');value['created_at']='different'
    assert content_hash(value)==m.bundle_content_hash


def test_schema_snapshot():
    path=Path(__file__).parents[2]/'schemas/types/book-bundle.schema.json'
    assert json.loads(path.read_text())==BookBundleManifest.model_json_schema()
