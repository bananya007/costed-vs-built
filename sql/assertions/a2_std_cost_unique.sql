-- One standard cost per item per cost version, or prices double-count.
SELECT item_id, cost_version, COUNT(*) AS n
FROM raw_warp_std_cost GROUP BY item_id, cost_version HAVING COUNT(*) > 1;
