-- Safe to run more than once. Run in Supabase: SQL Editor -> New query -> paste -> Run
create table if not exists owned_cards (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  card_id text not null,
  primary key (user_id, card_id)
);
alter table owned_cards enable row level security;
drop policy if exists "own rows read" on owned_cards;
create policy "own rows read" on owned_cards for select using (auth.uid() = user_id);
drop policy if exists "own rows insert" on owned_cards;
create policy "own rows insert" on owned_cards for insert with check (auth.uid() = user_id);
drop policy if exists "own rows delete" on owned_cards;
create policy "own rows delete" on owned_cards for delete using (auth.uid() = user_id);

-- Purchase info (price paid + currency). Run once more if you set the table up before this was added.
alter table owned_cards add column if not exists paid_price numeric;
alter table owned_cards add column if not exists paid_currency text check (paid_currency in ('IDR','JPY','USD'));
drop policy if exists "own rows update" on owned_cards;
create policy "own rows update" on owned_cards for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
