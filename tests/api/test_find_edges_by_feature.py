"""Unit tests for EntityManager.find_edges_by_feature (qCreatedBy edge query)."""

import pytest
from unittest.mock import AsyncMock

from onshape_mcp.api.entities import EntityManager


class TestFindEdgesByFeature:
    """Test the qCreatedBy-based edge lookup."""

    @pytest.fixture
    def entity_manager(self, onshape_client):
        """Provide an EntityManager instance."""
        return EntityManager(onshape_client)

    @pytest.mark.asyncio
    async def test_returns_edge_ids_and_count(
        self, entity_manager, onshape_client, sample_document_ids
    ):
        """Test that a successful FeatureScript response yields edge_ids + count."""
        onshape_client.post = AsyncMock(
            return_value={"result": {"value": ["JHA", "JHB", "JHC"]}}
        )

        result = await entity_manager.find_edges_by_feature(
            sample_document_ids["document_id"],
            sample_document_ids["workspace_id"],
            sample_document_ids["element_id"],
            "someFeatureId",
        )

        assert result == {"edge_ids": ["JHA", "JHB", "JHC"], "count": 3}
        onshape_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_result_when_feature_creates_no_edges(
        self, entity_manager, onshape_client, sample_document_ids
    ):
        """Test that an empty value list yields an empty edge_ids with count 0."""
        onshape_client.post = AsyncMock(return_value={"result": {"value": []}})

        result = await entity_manager.find_edges_by_feature(
            sample_document_ids["document_id"],
            sample_document_ids["workspace_id"],
            sample_document_ids["element_id"],
            "featureWithNoEdges",
        )

        assert result == {"edge_ids": [], "count": 0}

    @pytest.mark.asyncio
    async def test_malformed_response_returns_empty(
        self, entity_manager, onshape_client, sample_document_ids
    ):
        """Test that an unexpected response shape degrades to an empty result."""
        onshape_client.post = AsyncMock(return_value={"unexpected": "shape"})

        result = await entity_manager.find_edges_by_feature(
            sample_document_ids["document_id"],
            sample_document_ids["workspace_id"],
            sample_document_ids["element_id"],
            "featureId",
        )

        assert result == {"edge_ids": [], "count": 0}

    @pytest.mark.asyncio
    async def test_feature_id_embedded_in_query(
        self, entity_manager, onshape_client, sample_document_ids
    ):
        """Test that the feature_id is embedded in the FeatureScript qCreatedBy query."""
        onshape_client.post = AsyncMock(return_value={"result": {"value": []}})

        await entity_manager.find_edges_by_feature(
            sample_document_ids["document_id"],
            sample_document_ids["workspace_id"],
            sample_document_ids["element_id"],
            "myUniqueFeatureId123",
        )

        call_args = onshape_client.post.call_args
        script = call_args[1]["data"]["script"]
        assert "myUniqueFeatureId123" in script
        assert "qCreatedBy" in script
        assert "EntityType.EDGE" in script

    @pytest.mark.asyncio
    async def test_path_uses_v8_featurescript_endpoint(
        self, entity_manager, onshape_client, sample_document_ids
    ):
        """Test that the FeatureScript call hits the v8 featurescript endpoint."""
        onshape_client.post = AsyncMock(return_value={"result": {"value": []}})

        await entity_manager.find_edges_by_feature(
            sample_document_ids["document_id"],
            sample_document_ids["workspace_id"],
            sample_document_ids["element_id"],
            "featureId",
        )

        path = onshape_client.post.call_args[0][0]
        assert "/api/v8/" in path
        assert "/featurescript" in path
