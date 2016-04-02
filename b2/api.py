######################################################################
#
# File: b2/api.py
#
# Copyright 2016 Backblaze Inc. All Rights Reserved.
#
# License https://www.backblaze.com/using_b2_code.html
#
######################################################################

from .account_info import StoredAccountInfo
from .b2http import B2Http
from .bucket import Bucket, BucketFactory
from .cache import AuthInfoCache, DummyCache
from .exception import MissingAccountData, NonExistentBucket
from .file_version import FileVersionInfoFactory
from .part import PartFactory
from .raw_api import B2RawApi
from .session import B2Session


def url_for_api(info, api_name):
    if api_name in ['b2_download_file_by_id']:
        base = info.get_download_url()
    else:
        base = info.get_api_url()
    return base + '/b2api/v1/' + api_name


class B2Api(object):
    """
    Provides file-level access to B2 services.

    While B2RawApi provides direct access to the B2 web APIs, this
    class handles several things that simplify the task of uploading
    and downloading files:
      - re-acquires authorization tokens when they expire
      - retrying uploads when an upload URL is busy
      - breaking large files into parts
      - emulating a directory structure (B2 buckets are flat)

    Adds an object-oriented layer on top of the raw API, so that
    buckets and files returned are Python objects with accessor
    methods.

    Also,  keeps a cache of information needed to access the service,
    such as auth tokens and upload URLs.
    """

    # TODO: move HTTP code out to B2RawApi
    # TODO: ConsoleTool passes the account info cache into the constructor
    # TODO: provide method to get the account info cache (so ConsoleTool can save it)

    def __init__(self, account_info=None, cache=None, raw_api=None):
        """
        Initializes the API using the given account info.
        :param account_info:
        :param cache:
        :param raw_api:
        :return:
        """
        self.raw_api = raw_api or B2RawApi(B2Http())
        if account_info is None:
            account_info = StoredAccountInfo()
            if cache is None:
                cache = AuthInfoCache(account_info)
        self.session = B2Session(self, self.raw_api)
        self.account_info = account_info
        if cache is None:
            cache = DummyCache()
        self.cache = cache

    def authorize_automatically(self):
        try:
            self.authorize_account(
                self.account_info.get_realm(),
                self.account_info.get_account_id(),
                self.account_info.get_application_key(),
            )
        except MissingAccountData:
            return False
        return True

    def authorize_account(self, realm, account_id, application_key):
        try:
            old_account_id = self.account_info.get_account_id()
            old_realm = self.account_info.get_realm()
            if account_id != old_account_id or realm != old_realm:
                self.cache.clear()
        except MissingAccountData:
            self.cache.clear()

        realm_url = self.account_info.REALM_URLS[realm]
        response = self.raw_api.authorize_account(realm_url, account_id, application_key)

        self.account_info.set_auth_data(
            response['accountId'],
            response['authorizationToken'],
            response['apiUrl'],
            response['downloadUrl'],
            response['minimumPartSize'],
            application_key,
            realm,
        )

    def get_account_id(self):
        return self.account_info.get_account_id()

    # buckets

    def create_bucket(self, name, type_):
        account_id = self.account_info.get_account_id()

        response = self.session.create_bucket(account_id, name, type_)
        bucket = BucketFactory.from_api_bucket_dict(self, response)
        assert name == bucket.name, 'API created a bucket with different name\
                                     than requested: %s != %s' % (name, bucket.name)
        assert type_ == bucket.type_, 'API created a bucket with different type\
                                     than requested: %s != %s' % (type_, bucket.type_)
        self.cache.save_bucket(bucket)
        return bucket

    def download_file_by_id(self, file_id, download_dest):
        self.raw_api.download_file_by_id(
            self.account_info.get_download_url(), self.account_info.get_account_auth_token(),
            file_id, download_dest
        )

    def get_bucket_by_id(self, bucket_id):
        return Bucket(self, bucket_id)

    def get_bucket_by_name(self, bucket_name):
        """
        Returns the bucket_id for the given bucket_name.

        If we don't already know it from the cache, try fetching it from
        the B2 service.
        """
        # If we can get it from the stored info, do that.
        id_ = self.cache.get_bucket_id_or_none_from_bucket_name(bucket_name)
        if id_ is not None:
            return Bucket(self, id_, name=bucket_name)

        for bucket in self.list_buckets():
            if bucket.name == bucket_name:
                return bucket
        raise NonExistentBucket(bucket_name)

    def delete_bucket(self, bucket):
        """
        Deletes the bucket remotely.
        For legacy reasons it returns whatever server sends in response,
        but API user should not rely on the response: if it doesn't raise
        an exception, it means that the operation was a success
        """
        account_id = self.account_info.get_account_id()
        return self.session.delete_bucket(account_id, bucket.id_)

    def list_buckets(self):
        """
        Calls b2_list_buckets and returns the JSON for *all* buckets.
        """
        account_id = self.account_info.get_account_id()

        response = self.session.list_buckets(account_id)

        buckets = BucketFactory.from_api_response(self, response)

        self.cache.set_bucket_name_cache(buckets)
        return buckets

    def list_parts(self, file_id, start_part_number=None, batch_size=None):
        """
        Generator that yields a Part for each of the parts that have been uploaded.

        :param file_id: the ID of the large file that is not finished
        :param start_part_number: the first part number to return.  defaults to the first part.
        :param batch_size: the number of parts to fetch at a time from the server
        """
        batch_size = batch_size or 100
        while True:
            response = self.session.list_parts(file_id, start_part_number, batch_size)
            for part_dict in response['parts']:
                yield PartFactory.from_list_parts_dict(part_dict)
            start_part_number = response.get('nextPartNumber')
            if start_part_number is None:
                break

    # delete/cancel
    def cancel_large_file(self, file_id):
        response = self.session.cancel_large_file(file_id)
        return FileVersionInfoFactory.from_cancel_large_file_response(response)

    def delete_file_version(self, file_id, file_name):
        # filename argument is not first, because one day it may become optional
        response = self.session.delete_file_version(file_id, file_name)
        file_info = FileVersionInfoFactory.from_api_response(response, force_action='delete',)
        assert file_info.id_ == file_id
        assert file_info.file_name == file_name
        assert file_info.action == 'delete'
        return file_info

    # download
    def get_download_url_for_fileid(self, file_id):
        url = url_for_api(self.account_info, 'b2_download_file_by_id')
        return '%s?fileId=%s' % (url, file_id)

    # other
    def get_file_info(self, file_id):
        """ legacy interface which just returns whatever remote API returns """
        return self.session.get_file_info(file_id)
