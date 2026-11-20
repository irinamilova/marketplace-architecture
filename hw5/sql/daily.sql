SELECT
    grouping(movie_id) AS is_total,
    movie_id,
    count() AS source_events,
    uniqExact(user_id) AS dau,
    avgOrNullIf(progress_seconds, event_type = 'VIEW_FINISHED') AS avg_view_seconds,
    countIf(event_type = 'VIEW_STARTED') AS views_started,
    countIf(event_type = 'VIEW_FINISHED') AS views_finished
FROM cinema.raw_events FINAL
WHERE toDate(timestamp) = {day:Date}
GROUP BY GROUPING SETS ((movie_id), ())
ORDER BY is_total DESC, views_started DESC, movie_id
LIMIT 11
