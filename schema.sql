-- schema.sql: the tables in the Aurora PostgreSQL cluster "airbnb-users"
-- (database "postgres"). Both already exist; this file records them.
-- Safe to run again: it skips tables that already exist.

-- User accounts (log in / create account)
CREATE TABLE IF NOT EXISTS users (
  user_id      serial PRIMARY KEY,
  name         varchar,
  email        varchar,   -- used to log in
  home_city    varchar,
  member_since date
);

-- Airbnbs a user added from the search results ("Add to my Airbnbs")
CREATE TABLE IF NOT EXISTS user_airbnbs (
  id                serial PRIMARY KEY,
  user_id           integer NOT NULL REFERENCES users(user_id),
  address           varchar NOT NULL,      -- the property's key in DynamoDB
  formatted_address varchar,
  price_per_night   integer,
  beds              integer,
  baths             integer,
  added_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, address)  -- each Airbnb only once per user
);
