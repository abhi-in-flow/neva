-- Forward migration: case-insensitive unique player nicknames.
--
-- Remaps any existing lower(nickname) collisions before creating the unique
-- index so venues with duplicate demo names remain startable. Application
-- join logic preserves the requested friendly name when free and appends a
-- compact #N suffix on collision (see app/game/nicknames.py).

DO $$
DECLARE
    rec RECORD;
    candidate TEXT;
    suffix INT;
    max_base INT;
BEGIN
    FOR rec IN
        SELECT p.id, p.nickname
        FROM players p
        WHERE EXISTS (
            SELECT 1
            FROM players o
            WHERE lower(o.nickname) = lower(p.nickname)
              AND (o.created_at, o.id) < (p.created_at, p.id)
        )
        ORDER BY p.created_at ASC, p.id ASC
    LOOP
        suffix := 2;
        LOOP
            max_base := 32 - length('#' || suffix::text);
            IF max_base < 1 THEN
                candidate := right('#' || suffix::text, 32);
            ELSE
                candidate := left(rec.nickname, max_base);
                candidate := rtrim(candidate);
                IF candidate = '' THEN
                    candidate := 'p';
                END IF;
                candidate := left(candidate, max_base) || '#' || suffix::text;
            END IF;
            EXIT WHEN NOT EXISTS (
                SELECT 1 FROM players WHERE lower(nickname) = lower(candidate)
            );
            suffix := suffix + 1;
            IF suffix > 10000 THEN
                RAISE EXCEPTION
                    'unable to remap duplicate nickname for player %', rec.id;
            END IF;
        END LOOP;
        UPDATE players SET nickname = candidate WHERE id = rec.id;
    END LOOP;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS players_nickname_lower_uidx
    ON players (lower(nickname));
