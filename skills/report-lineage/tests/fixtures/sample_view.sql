-- A simple reporting view over the Sales/Region tables.
CREATE VIEW dbo.SalesByRegionView
AS
SELECT
    s.SaleId,
    s.Amount,
    r.RegionName
FROM dbo.Sales AS s
JOIN dbo.Region AS r ON s.RegionId = r.RegionId;
