import { Box, Text } from "ink";

/**
 * A friendly empty state for list views: an accented headline plus one or more
 * dim hint lines that tell the user how to fill it — dead space becomes
 * guidance. Distinct from a loading state (see callers) and from an error.
 */
export function EmptyState({
  icon = "◦",
  title,
  hints = [],
}: {
  icon?: string;
  title: string;
  hints?: string[];
}) {
  return (
    <Box flexDirection="column" marginTop={1} marginLeft={2}>
      <Text>
        <Text color="yellow">{icon} </Text>
        {title}
      </Text>
      {hints.map((h, i) => (
        <Text key={i} dimColor>
          {"  "}
          {h}
        </Text>
      ))}
    </Box>
  );
}
