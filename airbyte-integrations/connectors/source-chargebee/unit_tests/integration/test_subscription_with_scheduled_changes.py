# Copyright (c) 2023 Airbyte, Inc., all rights reserved.

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from unittest import TestCase

import freezegun
from source_chargebee import SourceChargebee

from airbyte_cdk.models import AirbyteStateBlob, ConfiguredAirbyteCatalog, FailureType, StreamDescriptor, SyncMode
from airbyte_cdk.test.catalog_builder import CatalogBuilder
from airbyte_cdk.test.entrypoint_wrapper import EntrypointOutput, read
from airbyte_cdk.test.mock_http import HttpMocker
from airbyte_cdk.test.mock_http.response_builder import (
    FieldPath,
    HttpResponseBuilder,
    NestedPath,
    Path,
    RecordBuilder,
    create_record_builder,
    create_response_builder,
    find_template,
)
from airbyte_cdk.test.state_builder import StateBuilder

from .config import ConfigBuilder
from .pagination import ChargebeePaginationStrategy
from .request_builder import ChargebeeRequestBuilder, ChargebeeSubstreamRequestBuilder
from .response_builder import a_response_with_status, a_response_with_status_and_header


_STREAM_NAME = "subscription_with_scheduled_changes"
_SITE = "test-site"
_SITE_API_KEY = "test-api-key"
_PRODUCT_CATALOG = "2.0"
_PRIMARY_KEY = "id"
_CURSOR_FIELD = "updated_at"
_NO_STATE = {}
_NOW = datetime.now(timezone.utc)


def _a_parent_request() -> ChargebeeRequestBuilder:
    return ChargebeeRequestBuilder.subscription_endpoint(_SITE, _SITE_API_KEY)


def _a_child_request() -> ChargebeeSubstreamRequestBuilder:
    return ChargebeeSubstreamRequestBuilder.subscription_with_scheduled_changes_endpoint(_SITE, _SITE_API_KEY)


def _config() -> ConfigBuilder:
    return ConfigBuilder().with_site(_SITE).with_site_api_key(_SITE_API_KEY).with_product_catalog(_PRODUCT_CATALOG)


def _catalog(sync_mode: SyncMode) -> ConfiguredAirbyteCatalog:
    return CatalogBuilder().with_stream(_STREAM_NAME, sync_mode).build()


def _source(catalog: ConfiguredAirbyteCatalog, config: Dict[str, Any], state: Optional[Dict[str, Any]]) -> SourceChargebee:
    return SourceChargebee(catalog=catalog, config=config, state=state)


def _a_parent_record() -> RecordBuilder:
    return create_record_builder(
        find_template("subscription", __file__),
        FieldPath("list"),
        record_id_path=NestedPath(["subscription", _PRIMARY_KEY]),
        record_cursor_path=NestedPath(["subscription", _CURSOR_FIELD]),
    )


def _a_child_record() -> RecordBuilder:
    return create_record_builder(
        find_template("subscription_with_scheduled_changes", __file__),
        FieldPath("list"),
        record_id_path=NestedPath(["subscription", _PRIMARY_KEY]),
        record_cursor_path=NestedPath(["subscription", _CURSOR_FIELD]),
    )


def _a_parent_response() -> HttpResponseBuilder:
    return create_response_builder(
        find_template("subscription", __file__), FieldPath("list"), pagination_strategy=ChargebeePaginationStrategy()
    )


def _a_child_response() -> HttpResponseBuilder:
    return create_response_builder(
        find_template("subscription_with_scheduled_changes", __file__), FieldPath("list"), pagination_strategy=ChargebeePaginationStrategy()
    )


def _read(
    config_builder: ConfigBuilder, sync_mode: SyncMode, state: Optional[Dict[str, Any]] = None, expecting_exception: bool = False
) -> EntrypointOutput:
    catalog = _catalog(sync_mode)
    config = config_builder.build()
    source = _source(catalog=catalog, config=config, state=state)
    return read(source, config, catalog, state, expecting_exception)


@freezegun.freeze_time(_NOW.isoformat())
class FullRefreshTest(TestCase):
    def setUp(self) -> None:
        self._now = _NOW
        self._now_in_seconds = int(self._now.timestamp())
        self._start_date = _NOW - timedelta(days=28)
        self._start_date_in_seconds = int(self._start_date.timestamp())

    @staticmethod
    def _read(config: ConfigBuilder, expecting_exception: bool = False) -> EntrypointOutput:
        return _read(config, SyncMode.full_refresh, expecting_exception=expecting_exception)

    @HttpMocker()
    def test_when_read_then_records_are_extracted(self, http_mocker: HttpMocker) -> None:
        parent_id = "subscription_test"

        http_mocker.get(
            _a_parent_request().with_any_query_params().build(),
            _a_parent_response().with_record(_a_parent_record().with_id(parent_id)).build(),
        )
        http_mocker.get(
            _a_child_request()
            .with_parent_id(parent_id)
            .with_endpoint_path("retrieve_with_scheduled_changes")
            .with_any_query_params()
            .build(),
            _a_child_response().with_record(_a_child_record()).build(),
        )

        output = self._read(_config().with_start_date(self._start_date))
        assert len(output.records) == 1

    @HttpMocker()
    def test_given_multiple_parents_when_read_then_fetch_for_each_parent(self, http_mocker: HttpMocker) -> None:
        a_parent_id = "a_subscription_test"
        another_parent_id = "another_subscription_test"

        http_mocker.get(
            _a_parent_request().with_any_query_params().build(),
            _a_parent_response()
            .with_record(_a_parent_record().with_id(a_parent_id))
            .with_record(_a_parent_record().with_id(another_parent_id))
            .build(),
        )

        http_mocker.get(
            _a_child_request()
            .with_parent_id(a_parent_id)
            .with_endpoint_path("retrieve_with_scheduled_changes")
            .with_any_query_params()
            .build(),
            _a_child_response().with_record(_a_child_record()).build(),
        )
        http_mocker.get(
            _a_child_request()
            .with_parent_id(another_parent_id)
            .with_endpoint_path("retrieve_with_scheduled_changes")
            .with_any_query_params()
            .build(),
            _a_child_response().with_record(_a_child_record()).build(),
        )

        output = self._read(_config().with_start_date(self._start_date))
        assert len(output.records) == 2

    @HttpMocker()
    def test_when_read_then_primary_key_is_set(self, http_mocker: HttpMocker) -> None:
        parent_id = "subscription_test"

        http_mocker.get(
            _a_parent_request().with_any_query_params().build(),
            _a_parent_response().with_record(_a_parent_record().with_id(parent_id)).build(),
        )
        http_mocker.get(
            _a_child_request()
            .with_parent_id(parent_id)
            .with_endpoint_path("retrieve_with_scheduled_changes")
            .with_any_query_params()
            .build(),
            _a_child_response().with_record(_a_child_record()).build(),
        )

        output = self._read(_config().with_start_date(self._start_date))
        assert "subscription_id" in output.records[0].record.data

    @HttpMocker()
    def test_given_http_status_400_when_read_then_stream_is_ignored(self, http_mocker: HttpMocker) -> None:
        parent_id = "subscription_test"

        http_mocker.get(
            _a_parent_request().with_any_query_params().build(),
            _a_parent_response().with_record(_a_parent_record().with_id(parent_id)).build(),
        )
        http_mocker.get(
            _a_child_request()
            .with_parent_id(parent_id)
            .with_endpoint_path("retrieve_with_scheduled_changes")
            .with_any_query_params()
            .build(),
            a_response_with_status(400),
        )

        self._read(_config().with_start_date(self._start_date), expecting_exception=True)

    @HttpMocker()
    def test_given_http_status_404_when_read_then_stream_is_ignored(self, http_mocker: HttpMocker) -> None:
        parent_id = "subscription_test"

        http_mocker.get(
            _a_parent_request().with_any_query_params().build(),
            _a_parent_response().with_record(_a_parent_record().with_id(parent_id)).build(),
        )
        http_mocker.get(
            _a_child_request()
            .with_parent_id(parent_id)
            .with_endpoint_path("retrieve_with_scheduled_changes")
            .with_any_query_params()
            .build(),
            a_response_with_status(404),
        )

        self._read(_config().with_start_date(self._start_date), expecting_exception=False)
