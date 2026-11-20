WITH {day:Date} AS cohort_day
SELECT
    count() AS cohort_size,
    sumForEach(arrayMap(offset -> toUInt64(has(active_days, offset)), range(8))) AS returned
FROM
(
    SELECT user_id,
           min(toDate(timestamp)) AS first_day,
           groupUniqArrayIf(
               dateDiff('day', cohort_day, toDate(timestamp)),
               toDate(timestamp) BETWEEN cohort_day AND addDays(cohort_day, 7)
           ) AS active_days
    FROM cinema.raw_events
    WHERE event_type = 'VIEW_STARTED'
      AND toDate(timestamp) <= addDays(cohort_day, 7)
    GROUP BY user_id
    HAVING first_day = cohort_day
)
